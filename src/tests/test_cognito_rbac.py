import dataclasses
from datetime import datetime, timedelta, timezone
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from jose.utils import base64url_encode

from bff.config import SECURITY_SETTINGS, validate_security_settings
from bff.postgres import scraper_routes
from bff.security import Role, SlidingWindowRateLimiter, require_roles


def _encoded_integer(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64url_encode(raw).decode("ascii")


class TestCognitoRbac(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_numbers = cls.private_key.public_key().public_numbers()
        cls.private_pem = cls.private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.other_private_pem = cls.other_private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        cls.issuer = "https://cognito-idp.eu-west-1.amazonaws.com/eu-west-1_testpool"
        cls.settings = dataclasses.replace(
            SECURITY_SETTINGS,
            app_env="demo",
            auth_mode="cognito_rbac",
            dev_auth_enabled=False,
            cognito_region="eu-west-1",
            cognito_user_pool_id="eu-west-1_testpool",
            cognito_client_id="test-public-client",
            cognito_domain="https://fip-test.auth.eu-west-1.amazoncognito.com",
            oidc_jwks_json=json.dumps(
                {
                    "keys": [
                        {
                            "kty": "RSA",
                            "kid": "cognito-test-key",
                            "use": "sig",
                            "alg": "RS256",
                            "n": _encoded_integer(public_numbers.n),
                            "e": _encoded_integer(public_numbers.e),
                        }
                    ]
                }
            ),
            oidc_algorithms=("RS256",),
        )
        validate_security_settings(cls.settings)
        application = FastAPI()
        application.state.security_settings = cls.settings
        application.state.rate_limiter = SlidingWindowRateLimiter(10_000, 60)

        @application.get("/normal/read", dependencies=[Depends(require_roles(Role.CUSTOMER))])
        async def read():
            return {"ok": True}

        @application.post("/api/scraper/run", dependencies=[Depends(require_roles(Role.OPERATOR))])
        async def scraper_run():
            return {"ok": True}

        @application.post(
            "/api/admin/pipeline/trigger",
            dependencies=[Depends(require_roles(Role.OPERATOR))],
        )
        async def pipeline_trigger():
            return {"ok": True}

        @application.get("/api/admin/guard", dependencies=[Depends(require_roles(Role.ADMIN))])
        async def admin():
            return {"ok": True}

        class FakeJobs:
            async def latest_status(self):
                return {
                    "status": "success",
                    "started_at": "2026-08-12T10:00:00+00:00",
                    "finished_at": "2026-08-12T10:05:00+00:00",
                }

        class FakePipelines:
            async def public_source_statuses(self):
                return [
                    {
                        "name": "approved-source",
                        "enabled": True,
                        "freshness_sla_hours": 24,
                        "last_success_at": "2026-08-12T10:05:00+00:00",
                        "record_count": 42,
                    }
                ]

        application.include_router(scraper_routes.router)
        application.dependency_overrides[scraper_routes._jobs] = FakeJobs
        application.dependency_overrides[scraper_routes._pipelines] = FakePipelines

        cls.client = TestClient(application)

    def token(
        self,
        groups=("customer",),
        *,
        issuer=None,
        client_id="test-public-client",
        token_use="access",
        expires_delta=timedelta(minutes=5),
        kid="cognito-test-key",
        private_pem=None,
    ) -> str:
        now = datetime.now(timezone.utc)
        return jwt.encode(
            {
                "sub": "cognito-subject",
                "username": "opaque-cognito-username",
                "cognito:groups": list(groups),
                "iss": issuer or self.issuer,
                "client_id": client_id,
                "token_use": token_use,
                "iat": now,
                "exp": now + expires_delta,
            },
            private_pem or self.private_pem,
            algorithm="RS256",
            headers={"kid": kid},
        )

    def request(self, path: str, token: str | None, method="GET"):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return self.client.request(method, path, headers=headers)

    def test_valid_access_token_and_role_permissions(self):
        customer = self.token()
        self.assertEqual(self.request("/normal/read", customer).status_code, 200)
        status_response = self.request("/api/scraper/status", customer)
        self.assertEqual(status_response.status_code, 200)
        self.assertEqual(status_response.json()["sources"][0]["record_count"], 42)
        self.assertEqual(self.request("/api/scraper/run", customer, "POST").status_code, 403)
        self.assertEqual(
            self.request("/api/admin/pipeline/trigger", customer, "POST").status_code,
            403,
        )
        self.assertEqual(self.request("/api/admin/guard", customer).status_code, 403)
        operator = self.token(("operator",))
        self.assertEqual(self.request("/normal/read", operator).status_code, 200)
        self.assertEqual(self.request("/api/scraper/status", operator).status_code, 200)
        self.assertEqual(self.request("/api/scraper/run", operator, "POST").status_code, 200)
        self.assertEqual(
            self.request("/api/admin/pipeline/trigger", operator, "POST").status_code,
            200,
        )
        self.assertEqual(self.request("/api/admin/guard", operator).status_code, 403)
        admin = self.token(("admin",))
        self.assertEqual(self.request("/normal/read", admin).status_code, 200)
        self.assertEqual(self.request("/api/scraper/status", admin).status_code, 200)
        self.assertEqual(self.request("/api/scraper/run", admin, "POST").status_code, 200)
        self.assertEqual(self.request("/api/admin/guard", admin).status_code, 200)

    def test_invalid_access_tokens_fail_with_401(self):
        cases = {
            "expired": self.token(expires_delta=timedelta(seconds=-1)),
            "wrong_signature": self.token(private_pem=self.other_private_pem),
            "unknown_kid": self.token(kid="unknown"),
            "wrong_issuer": self.token(issuer="https://identity.example.invalid"),
            "wrong_client": self.token(client_id="another-client"),
            "id_token": self.token(token_use="id"),
        }
        valid = self.token()
        cases["tampered"] = valid[:-2] + ("aa" if valid[-2:] != "aa" else "bb")
        for name, token in cases.items():
            with self.subTest(name=name):
                self.assertEqual(self.request("/normal/read", token).status_code, 401)
        self.assertEqual(self.request("/normal/read", None).status_code, 401)

    def test_zero_or_multiple_app_groups_fail_with_403(self):
        memberships = (
            (),
            ("unrelated",),
            ("customer", "operator"),
            ("customer", "admin"),
            ("operator", "admin"),
            ("customer", "operator", "admin"),
        )
        for groups in memberships:
            with self.subTest(groups=groups):
                self.assertEqual(
                    self.request("/normal/read", self.token(groups)).status_code,
                    403,
                )


if __name__ == "__main__":
    unittest.main()
