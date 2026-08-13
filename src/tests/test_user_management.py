import dataclasses
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from bff.config import SECURITY_SETTINGS
from bff.security import IdempotencyStore, Principal, Role, SlidingWindowRateLimiter
from bff.user_management import router


class FakeCognitoError(Exception):
    def __init__(self, code: str):
        self.response = {"Error": {"Code": code}}
        super().__init__(code)


class FakeCognitoClient:
    def __init__(self):
        self.users = {
            "admin-one": {
                "Username": "admin-one",
                "Enabled": True,
                "UserStatus": "CONFIRMED",
                "Attributes": [
                    {"Name": "sub", "Value": "sub-admin-one"},
                    {"Name": "email", "Value": "admin1@example.test"},
                ],
            },
            "admin-two": {
                "Username": "admin-two",
                "Enabled": True,
                "UserStatus": "CONFIRMED",
                "Attributes": [
                    {"Name": "sub", "Value": "sub-admin-two"},
                    {"Name": "email", "Value": "admin2@example.test"},
                ],
            },
            "customer-one": {
                "Username": "customer-one",
                "Enabled": True,
                "UserStatus": "CONFIRMED",
                "Attributes": [
                    {"Name": "sub", "Value": "sub-customer-one"},
                    {"Name": "email", "Value": "customer@example.test"},
                ],
            },
        }
        self.groups = {
            "admin-one": {"admin"},
            "admin-two": {"admin"},
            "customer-one": {"customer"},
        }
        self.fail_list = False

    def list_users(self, **kwargs):
        if self.fail_list:
            raise FakeCognitoError("InternalErrorException")
        names = sorted(self.users)
        start = int(kwargs.get("PaginationToken", "0"))
        limit = int(kwargs["Limit"])
        selected = names[start : start + limit]
        response = {"Users": [self.users[name] for name in selected]}
        if start + limit < len(names):
            response["PaginationToken"] = str(start + limit)
        return response

    def admin_list_groups_for_user(self, **kwargs):
        groups = sorted(self.groups[kwargs["Username"]])
        start = int(kwargs.get("NextToken", "0"))
        selected = groups[start : start + 1]
        response = {"Groups": [{"GroupName": group} for group in selected]}
        if start + 1 < len(groups):
            response["NextToken"] = str(start + 1)
        return response

    def admin_get_user(self, **kwargs):
        username = kwargs["Username"]
        if username not in self.users:
            raise FakeCognitoError("UserNotFoundException")
        return self.users[username]

    def admin_create_user(self, **kwargs):
        email = kwargs["Username"]
        if any(
            attribute["Value"] == email
            for user in self.users.values()
            for attribute in user["Attributes"]
            if attribute["Name"] == "email"
        ):
            raise FakeCognitoError("UsernameExistsException")
        username = f"opaque-{len(self.users) + 1}"
        user = {
            "Username": username,
            "Enabled": True,
            "UserStatus": "FORCE_CHANGE_PASSWORD",
            "Attributes": [
                {"Name": "sub", "Value": f"sub-{username}"},
                {"Name": "email", "Value": email},
            ],
        }
        self.users[username] = user
        self.groups[username] = set()
        return {"User": user}

    def admin_add_user_to_group(self, **kwargs):
        self.groups[kwargs["Username"]].add(kwargs["GroupName"])

    def admin_remove_user_from_group(self, **kwargs):
        self.groups[kwargs["Username"]].discard(kwargs["GroupName"])

    def list_users_in_group(self, **kwargs):
        names = sorted(
            name for name, groups in self.groups.items() if kwargs["GroupName"] in groups
        )
        start = int(kwargs.get("NextToken", "0"))
        selected = names[start : start + 1]
        response = {"Users": [self.users[name] for name in selected]}
        if start + 1 < len(names):
            response["NextToken"] = str(start + 1)
        return response

    def admin_disable_user(self, **kwargs):
        self.users[kwargs["Username"]]["Enabled"] = False

    def admin_enable_user(self, **kwargs):
        self.users[kwargs["Username"]]["Enabled"] = True

    def admin_reset_user_password(self, **kwargs):
        self.users[kwargs["Username"]]["UserStatus"] = "RESET_REQUIRED"


class TestUserManagement(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCognitoClient()
        application = FastAPI()
        application.include_router(router)
        application.state.security_settings = dataclasses.replace(
            SECURITY_SETTINGS,
            app_env="demo",
            auth_mode="cognito_rbac",
            dev_auth_enabled=False,
            cognito_region="eu-west-1",
            cognito_user_pool_id="eu-west-1_testpool",
            cognito_client_id="client",
            cognito_domain="https://fip-test.auth.eu-west-1.amazoncognito.com",
        )
        application.state.cognito_client = self.fake
        application.state.rate_limiter = SlidingWindowRateLimiter(10_000, 60)
        application.state.idempotency_store = IdempotencyStore()
        self.client = TestClient(application)

    @staticmethod
    def principal(role: Role, subject="admin-caller") -> Principal:
        return Principal(subject, frozenset({role}), {"sub": subject})

    def call(self, method, path, role=Role.ADMIN, subject="admin-caller", **kwargs):
        with patch(
            "bff.security.authenticate_request",
            new=AsyncMock(return_value=self.principal(role, subject)),
        ):
            return self.client.request(method, path, **kwargs)

    @staticmethod
    def mutation_headers(index: str):
        return {"Idempotency-Key": f"user-management-{index}"}

    def test_pagination_next_token_and_minimal_attributes(self):
        first = self.call("GET", "/api/admin/users?page_size=2")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["users"]), 2)
        self.assertEqual(first.json()["next_token"], "2")
        second = self.call("GET", "/api/admin/users?page_size=2&next_token=2")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(second.json()["users"]), 1)
        for user in first.json()["users"] + second.json()["users"]:
            self.assertEqual(set(user), {"id", "email", "enabled", "status", "role"})

    def test_multiple_application_groups_are_detected_across_pages(self):
        self.fake.groups["customer-one"].add("operator")
        response = self.call("GET", "/api/admin/users?page_size=3")
        self.assertEqual(response.status_code, 200)
        customer = next(user for user in response.json()["users"] if user["id"] == "customer-one")
        self.assertIsNone(customer["role"])

    def test_invite_permissions_and_duplicate_error(self):
        payload = {"email": "new@example.test", "role": "customer"}
        for role in (Role.CUSTOMER, Role.OPERATOR):
            response = self.call(
                "POST",
                "/api/admin/users",
                role=role,
                json=payload,
                headers=self.mutation_headers(role.value),
            )
            self.assertEqual(response.status_code, 403)
        created = self.call(
            "POST",
            "/api/admin/users",
            json=payload,
            headers=self.mutation_headers("create"),
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["role"], "customer")
        duplicate = self.call(
            "POST",
            "/api/admin/users",
            json=payload,
            headers=self.mutation_headers("duplicate"),
        )
        self.assertEqual(duplicate.status_code, 409)

    def test_invalid_role_and_provider_failure_are_safe(self):
        invalid = self.call(
            "POST",
            "/api/admin/users",
            json={"email": "new@example.test", "role": "superuser"},
            headers=self.mutation_headers("invalid"),
        )
        self.assertEqual(invalid.status_code, 400)
        self.fake.fail_list = True
        failed = self.call("GET", "/api/admin/users")
        self.assertEqual(failed.status_code, 502)
        self.assertNotIn("InternalError", failed.text)

    def test_last_admin_and_self_protection(self):
        self.fake.users["admin-two"]["Enabled"] = False
        last_disable = self.call(
            "POST",
            "/api/admin/users/admin-one/disable",
            headers=self.mutation_headers("last-disable"),
        )
        self.assertEqual(last_disable.status_code, 409)
        last_downgrade = self.call(
            "PATCH",
            "/api/admin/users/admin-one/role",
            json={"role": "operator"},
            headers=self.mutation_headers("last-downgrade"),
        )
        self.assertEqual(last_downgrade.status_code, 409)

        self.fake.users["admin-two"]["Enabled"] = True
        self_disable = self.call(
            "POST",
            "/api/admin/users/admin-one/disable",
            subject="sub-admin-one",
            headers=self.mutation_headers("self-disable"),
        )
        self.assertEqual(self_disable.status_code, 409)
        self_downgrade = self.call(
            "PATCH",
            "/api/admin/users/admin-one/role",
            subject="sub-admin-one",
            json={"role": "operator"},
            headers=self.mutation_headers("self-downgrade"),
        )
        self.assertEqual(self_downgrade.status_code, 409)

    def test_other_admin_can_be_disabled_or_downgraded_when_two_are_active(self):
        disabled = self.call(
            "POST",
            "/api/admin/users/admin-two/disable",
            headers=self.mutation_headers("other-disable"),
        )
        self.assertEqual(disabled.status_code, 200)
        self.fake.users["admin-two"]["Enabled"] = True
        downgraded = self.call(
            "PATCH",
            "/api/admin/users/admin-two/role",
            json={"role": "operator"},
            headers=self.mutation_headers("other-downgrade"),
        )
        self.assertEqual(downgraded.status_code, 200)
        self.assertEqual(downgraded.json()["role"], "operator")


if __name__ == "__main__":
    unittest.main()
