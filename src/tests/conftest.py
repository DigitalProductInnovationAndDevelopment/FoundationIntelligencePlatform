"""Explicit, local-only security configuration for the test process."""

import os


os.environ.update(
    {
        "APP_ENV": "test",
        "AUTH_MODE": "development",
        "DEV_AUTH_ENABLED": "true",
        "DEV_AUTH_USERNAME": "admin",
        "DEV_AUTH_PASSWORD": "password",
        "DEV_AUTH_SECRET": "unit-test-signing-key-with-at-least-32-characters",
        "DEV_AUTH_ALLOWED_HOSTS": "testclient,127.0.0.1,::1,localhost",
        "SESSION_COOKIE_SECURE": "false",
        "RATE_LIMIT_REQUESTS": "10000",
        "CORE_PROXY_ENABLED": "true",
        "CORE_API_URL": "http://127.0.0.1:8080",
        "CORE_API_ALLOWED_HOSTS": "127.0.0.1",
        "CORE_PROXY_ALLOWED_PATHS": "v1/data,users",
        "CORE_PROXY_ALLOWED_METHODS": "GET",
        "CORE_PROXY_FORWARD_HEADERS": "accept,content-type,x-request-id",
    }
)
