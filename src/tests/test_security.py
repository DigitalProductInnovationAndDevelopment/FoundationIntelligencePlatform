import dataclasses
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import base64url_encode

from bff import admin as admin_module
from bff.audit import MemoryAuditSink
from bff.config import (
    SECURITY_SETTINGS,
    SecurityConfigurationError,
    SecuritySettings,
    validate_security_settings,
)
from bff.main import app
from bff.security import (
    IdempotencyStore,
    PUBLIC_READONLY_METHOD_ALLOWLIST,
    PUBLIC_READONLY_ROUTE_ALLOWLIST,
    Role,
    SlidingWindowRateLimiter,
    create_development_access_token,
)
from bff.utils.logging import RedactingFormatter, redact_text


class TestSecurityGate(unittest.TestCase):
    def setUp(self):
        self.original_settings = app.state.security_settings
        self.original_limiter = app.state.rate_limiter
        self.original_idempotency_store = app.state.idempotency_store
        self.original_audit_sink = app.state.audit_sink
        app.state.security_settings = SECURITY_SETTINGS
        app.state.rate_limiter = SlidingWindowRateLimiter(10_000, 60)
        app.state.idempotency_store = IdempotencyStore()
        self.audit_sink = MemoryAuditSink()
        app.state.audit_sink = self.audit_sink
        self.admin_temp_dir = tempfile.TemporaryDirectory()
        self.admin_paths = patch.multiple(
            admin_module,
            STATUS_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_status.json"),
            LOCK_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.lock"),
            LOG_FILE=os.path.join(self.admin_temp_dir.name, "pipeline_run.log"),
        )
        self.admin_paths.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.admin_paths.stop()
        self.admin_temp_dir.cleanup()
        app.state.security_settings = self.original_settings
        app.state.rate_limiter = self.original_limiter
        app.state.idempotency_store = self.original_idempotency_store
        app.state.audit_sink = self.original_audit_sink

    def token(self, role: Role, subject: str = "security-test-user") -> str:
        return create_development_access_token(subject, (role,))

    def authorization(self, role: Role, subject: str = "security-test-user") -> dict:
        return {"Authorization": f"Bearer {self.token(role, subject)}"}

    def test_protected_and_admin_routes_reject_anonymous_requests(self):
        self.assertEqual(self.client.get("/api/charities").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/pipeline/status").status_code, 401)
        mutation_cases = (
            ("/api/admin/pipeline/trigger", {"source": "quick_consolidate"}),
            ("/api/charities/directory/organizations/enrich", {"reg_numbers": [1]}),
            ("/api/charities/grants/funders/enrich", {"reg_numbers": [1]}),
            ("/api/charities/grants/funders/source/profile-cache", None),
            ("/api/charities/grants/funders/source/reset-to-observed", None),
            ("/api/charities/grants/funders/source/relink", {"profile_id": 1}),
            ("/api/charities/1/score", {}),
            ("/api/core/v1/data", {}),
        )
        for index, (path, payload) in enumerate(mutation_cases):
            with self.subTest(path=path):
                response = self.client.post(
                    path,
                    json=payload,
                    headers={"Idempotency-Key": f"anonymous-{index}"},
                )
                self.assertEqual(response.status_code, 401)

    def test_viewer_is_forbidden_and_operator_is_authorized(self):
        forbidden = self.client.get(
            "/api/admin/pipeline/status",
            headers=self.authorization(Role.VIEWER),
        )
        allowed = self.client.get(
            "/api/admin/pipeline/status",
            headers=self.authorization(Role.OPERATOR),
        )
        self.assertEqual(forbidden.status_code, 403)
        self.assertEqual(allowed.status_code, 200)

    def test_oidc_signature_claims_and_role_are_validated(self):
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_numbers = private_key.public_key().public_numbers()

        def encoded_integer(value: int) -> str:
            raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
            return base64url_encode(raw).decode("ascii")

        jwks = {
            "keys": [
                {
                    "kty": "RSA",
                    "kid": "security-test-key",
                    "use": "sig",
                    "alg": "RS256",
                    "n": encoded_integer(public_numbers.n),
                    "e": encoded_integer(public_numbers.e),
                }
            ]
        }
        app.state.security_settings = dataclasses.replace(
            SECURITY_SETTINGS,
            auth_mode="oidc",
            oidc_issuer="https://identity.example.invalid/",
            oidc_audience="foundation-intelligence-api",
            oidc_jwks_json=json.dumps(jwks),
            oidc_algorithms=("RS256",),
        )
        now = datetime.now(timezone.utc)
        private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        token = jwt.encode(
            {
                "sub": "oidc-operator",
                "roles": ["operator"],
                "iss": "https://identity.example.invalid/",
                "aud": "foundation-intelligence-api",
                "iat": now,
                "exp": now + timedelta(minutes=5),
            },
            private_pem,
            algorithm="RS256",
            headers={"kid": "security-test-key"},
        )
        response = self.client.get(
            "/api/admin/pipeline/status",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.audit_sink.events[-1].actor_id, "oidc-operator")

    def test_request_id_and_complete_audit_event_are_created(self):
        response = self.client.get(
            "/api/admin/pipeline/status",
            headers={**self.authorization(Role.OPERATOR, "operator-42"), "X-Request-ID": "request-42"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "request-42")
        event = self.audit_sink.events[-1]
        self.assertEqual(event.actor_id, "operator-42")
        self.assertEqual(event.actor_role, "operator")
        self.assertEqual(event.action, "administration.access")
        self.assertEqual(event.target, "/api/admin/pipeline/status")
        self.assertEqual(event.reason, "not_provided")
        self.assertEqual(event.request_id, "request-42")
        self.assertEqual(event.result, "success")
        self.assertIsNone(event.error_class)
        self.assertIsNone(event.dataset_version)

    @patch("httpx.AsyncClient.request")
    def test_proxy_allowlist_and_forwarded_header_allowlist(self, mock_request):
        downstream = MagicMock()
        downstream.status_code = 200
        downstream.content = b'{"ok":true}'
        downstream.headers = {
            "Content-Type": "application/json",
            "Set-Cookie": "downstream=forbidden",
        }
        mock_request.return_value = downstream
        response = self.client.get(
            "/api/core/v1/data",
            headers={
                **self.authorization(Role.ADMINISTRATOR),
                "X-Forbidden-Forward": "secret-shaped-value",
            },
        )
        self.assertEqual(response.status_code, 200)
        sent_headers = mock_request.call_args.kwargs["headers"]
        self.assertNotIn("authorization", sent_headers)
        self.assertNotIn("x-forbidden-forward", sent_headers)
        self.assertNotIn("set-cookie", {key.lower() for key in response.headers})

        forbidden_path = self.client.get(
            "/api/core/private/admin",
            headers=self.authorization(Role.ADMINISTRATOR),
        )
        self.assertEqual(forbidden_path.status_code, 403)

    def test_rate_limit_returns_429(self):
        app.state.rate_limiter = SlidingWindowRateLimiter(1, 60)
        headers = self.authorization(Role.OPERATOR, "rate-limited-operator")
        self.assertEqual(self.client.get("/api/admin/pipeline/status", headers=headers).status_code, 200)
        limited = self.client.get("/api/admin/pipeline/status", headers=headers)
        self.assertEqual(limited.status_code, 429)
        self.assertIn("Retry-After", limited.headers)

    @patch("bff.admin.run_pipeline_task")
    def test_mutation_requires_idempotency_and_rejects_replay(self, run_pipeline_task):
        headers = self.authorization(Role.OPERATOR, "idempotent-operator")
        missing = self.client.post(
            "/api/admin/pipeline/trigger",
            json={"source": "quick_consolidate"},
            headers=headers,
        )
        self.assertEqual(missing.status_code, 400)

        headers = {**headers, "Idempotency-Key": "pipeline-once"}
        first = self.client.post(
            "/api/admin/pipeline/trigger",
            json={"source": "quick_consolidate"},
            headers=headers,
        )
        second = self.client.post(
            "/api/admin/pipeline/trigger",
            json={"source": "quick_consolidate"},
            headers=headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 409)
        run_pipeline_task.assert_called_once()

    def test_payload_limit_returns_413_before_handler(self):
        app.state.security_settings = dataclasses.replace(
            SECURITY_SETTINGS,
            max_request_body_bytes=32,
        )
        response = self.client.post(
            "/api/auth/login",
            content=b"x" * 33,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)

    def test_sensitive_values_are_redacted(self):
        value = redact_text(
            "Authorization: Bearer example-token password=example-password "
            "postgresql://app:database-password@db.internal/name "
            "{\"token\":\"json-token\"}"
        )
        self.assertNotIn("example-token", value)
        self.assertNotIn("example-password", value)
        self.assertNotIn("database-password", value)
        self.assertNotIn("json-token", value)
        self.assertGreaterEqual(value.count("[REDACTED]"), 4)

        try:
            raise RuntimeError("Authorization: Bearer traceback-token")
        except RuntimeError:
            record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", (), sys.exc_info())
        formatted = RedactingFormatter("%(message)s").format(record)
        self.assertNotIn("traceback-token", formatted)
        self.assertIn("[REDACTED]", formatted)

    def test_public_endpoint_is_read_only(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.post("/health").status_code, 405)

    def test_public_readonly_allows_only_reviewed_ui_read_routes(self):
        app.state.security_settings = dataclasses.replace(
            SECURITY_SETTINGS,
            app_env="demo",
            data_runtime_mode="postgresql",
            auth_mode="public_readonly",
            dev_auth_enabled=False,
            core_proxy_enabled=False,
        )

        allowed = self.client.get("/api/charities/grants/overview")
        self.assertEqual(allowed.status_code, 200)
        self.assertIsNone(allowed.headers.get("set-cookie"))
        self.assertEqual(
            PUBLIC_READONLY_ROUTE_ALLOWLIST,
            {
                "/api/charities",
                "/api/charities/stats",
                "/api/charities/{reg_charity_number}",
                "/api/charities/{reg_charity_number}/grants",
                "/api/charities/{reg_charity_number}/sankey",
                "/api/charities/{reg_charity_number}/score",
                "/api/charities/directory/organizations",
                "/api/charities/directory/organizations/{registry_id}",
                "/api/charities/grants/beneficiary-geographies",
                "/api/charities/grants/funders",
                "/api/charities/grants/funders/{source_funder_key}",
                "/api/charities/grants/map",
                "/api/charities/grants/map/connections",
                "/api/charities/grants/overview",
                "/api/charities/grants/overview/drilldown",
                "/api/charities/grants/overview/entity-suggestions",
                "/api/charities/grants/overview/trends",
                "/api/charities/grants/summary",
                "/api/charities/grants/themes",
                "/api/charities/grants/trends",
                "/api/scraper/status",
            },
        )
        self.assertEqual(PUBLIC_READONLY_METHOD_ALLOWLIST, {"GET", "HEAD"})

        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                blocked = self.client.request(
                    method,
                    "/api/charities/grants/overview",
                )
                self.assertEqual(blocked.status_code, 401)
                self.assertIsNone(blocked.headers.get("set-cookie"))

        protected = (
            "/api/charities/grants/funders/example/profile-cache",
            "/api/admin/pipeline/status",
            "/api/news/example/summary",
        )
        for path in protected:
            with self.subTest(path=path):
                self.assertIn(self.client.get(path).status_code, {401, 403})

        mutation = self.client.post(
            "/api/admin/pipeline/trigger",
            json={"source": "quick_consolidate"},
            headers={"Idempotency-Key": "public-demo-write"},
        )
        self.assertIn(mutation.status_code, {401, 403})

    def test_public_readonly_fails_closed_outside_demo(self):
        for environment in ("development", "test", "staging", "production"):
            with self.subTest(environment=environment):
                settings = SecuritySettings.from_env(
                    {
                        "APP_ENV": environment,
                        "DATA_RUNTIME_MODE": "postgresql",
                        "AUTH_MODE": "public_readonly",
                        "SESSION_COOKIE_SECURE": "true",
                        "CORS_ORIGINS": "https://app.example.invalid",
                    }
                )
                with self.assertRaisesRegex(
                    SecurityConfigurationError,
                    "public_readonly authentication requires APP_ENV=demo",
                ):
                    validate_security_settings(settings)

    def test_public_readonly_rejects_development_auth_and_proxy_bypasses(self):
        settings = SecuritySettings.from_env(
            {
                "APP_ENV": "demo",
                "DATA_RUNTIME_MODE": "postgresql",
                "AUTH_MODE": "public_readonly",
                "DEV_AUTH_ENABLED": "true",
                "CORE_PROXY_ENABLED": "true",
                "CORE_API_URL": "https://core.example.invalid",
                "CORE_API_ALLOWED_HOSTS": "core.example.invalid",
                "CORE_PROXY_ALLOWED_PATHS": "v1/data",
                "CORS_ORIGINS": "",
            }
        )
        with self.assertRaises(SecurityConfigurationError):
            validate_security_settings(settings)

    def test_public_readonly_rejects_legacy_data_runtime(self):
        settings = SecuritySettings.from_env(
            {
                "APP_ENV": "demo",
                "DATA_RUNTIME_MODE": "sqlite_migration_source",
                "AUTH_MODE": "public_readonly",
                "DEV_AUTH_ENABLED": "false",
                "CORE_PROXY_ENABLED": "false",
                "CORS_ORIGINS": "",
            }
        )
        with self.assertRaisesRegex(
            SecurityConfigurationError,
            "public_readonly authentication requires DATA_RUNTIME_MODE=postgresql",
        ):
            validate_security_settings(settings)

    def test_production_configuration_fails_closed(self):
        settings = SecuritySettings.from_env(
            {
                "APP_ENV": "production",
                "AUTH_MODE": "disabled",
                "SESSION_COOKIE_SECURE": "false",
            }
        )
        with self.assertRaises(SecurityConfigurationError):
            validate_security_settings(settings)
        app.state.security_settings = settings
        with self.assertRaises(SecurityConfigurationError):
            with TestClient(app):
                pass
        app.state.security_settings = SECURITY_SETTINGS

        unsafe_bypass = SecuritySettings.from_env(
            {
                "APP_ENV": "production",
                "AUTH_MODE": "development",
                "DEV_AUTH_ENABLED": "true",
                "DEV_AUTH_USERNAME": "local-user",
                "DEV_AUTH_PASSWORD": "local-password",
                "DEV_AUTH_SECRET": "local-signing-key-with-at-least-32-characters",
                "SESSION_COOKIE_SECURE": "true",
                "CORS_ORIGINS": "https://app.example.invalid",
            }
        )
        with self.assertRaises(SecurityConfigurationError):
            validate_security_settings(unsafe_bypass)


if __name__ == "__main__":
    unittest.main()
