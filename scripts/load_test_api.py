#!/usr/bin/env python3
"""Local in-process production API load test without external calls."""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import os
from time import perf_counter

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from jose import jwt
from jose.utils import base64url_encode
from sqlalchemy import text


def _configure_production_auth() -> str:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = private_key.public_key().public_numbers()

    def encoded(value: int) -> str:
        payload = value.to_bytes((value.bit_length() + 7) // 8, "big")
        return base64url_encode(payload).decode("ascii")

    os.environ.update(
        {
            "APP_ENV": "production",
            "AUTH_MODE": "oidc",
            "OIDC_ISSUER": "https://load-test.identity.invalid/",
            "OIDC_AUDIENCE": "foundation-intelligence-api",
            "OIDC_JWKS_JSON": json.dumps(
                {
                    "keys": [
                        {
                            "kty": "RSA",
                            "kid": "local-load-test",
                            "use": "sig",
                            "alg": "RS256",
                            "n": encoded(numbers.n),
                            "e": encoded(numbers.e),
                        }
                    ]
                }
            ),
            "CORS_ORIGINS": "https://load-test.app.invalid",
            "DEV_AUTH_ENABLED": "false",
            "SESSION_COOKIE_SECURE": "true",
            "CORE_PROXY_ENABLED": "false",
            "RATE_LIMIT_REQUESTS": "10000",
        }
    )
    now = datetime.now(timezone.utc)
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(
        {
            "sub": "local-load-test",
            "roles": ["viewer"],
            "iss": os.environ["OIDC_ISSUER"],
            "aud": os.environ["OIDC_AUDIENCE"],
            "iat": now,
            "exp": now + timedelta(minutes=10),
        },
        private_pem,
        algorithm="RS256",
        headers={"kid": "local-load-test"},
    )


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(len(ordered) * percentile)) - 1]


async def run(samples: int, concurrency: int) -> dict[str, object]:
    token = _configure_production_auth()
    from bff.audit import MemoryAuditSink
    from bff.main import app
    from bff.postgres.base import ANALYTICS_CACHE
    from bff.utils.logging import logger

    logger.setLevel("WARNING")

    headers = {"Authorization": f"Bearer {token}"}
    async with app.router.lifespan_context(app):
        app.state.audit_sink = MemoryAuditSink()
        async with app.state.database.sessions()() as session:
            dataset_version = await session.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            registered_name = await session.scalar(
                text(
                    """
                    SELECT registered_name FROM charity_registry_organizations
                    WHERE dataset_version=:dataset_version AND is_current_source_record
                    ORDER BY registry_id LIMIT 1
                    """
                ),
                {"dataset_version": dataset_version},
            )
            country = await session.scalar(
                text(
                    """
                    SELECT country_code FROM analytics_country_funder_rankings
                    WHERE dataset_version=:dataset_version
                    GROUP BY country_code ORDER BY COUNT(*) DESC LIMIT 1
                    """
                ),
                {"dataset_version": dataset_version},
            )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="https://load-test.app.invalid",
            headers=headers,
            timeout=30,
        ) as client:
            endpoints = {
                "health": "/health/ready",
                "organization_list": "/api/charities?limit=20",
                "default_map": "/api/charities/grants/map",
                "map_connections": (
                    "/api/charities/grants/map/connections?limit=250"
                ),
                "overview": "/api/charities/grants/overview",
                "yearly_trends": (
                    "/api/charities/grants/overview/trends?"
                    "granularity=yearly"
                ),
                "registry_exact": (
                    "/api/charities/directory/organizations?"
                    + str(httpx.QueryParams({"query": str(registered_name)}))
                ),
                "registry_text": (
                    "/api/charities/directory/organizations?"
                    + str(
                        httpx.QueryParams(
                            {"query": str(registered_name).split()[0]}
                        )
                    )
                ),
                "funder_ranking": (
                    "/api/charities/grants/funders?beneficiary_country="
                    + str(country)
                ),
            }
            results: dict[str, object] = {}
            for name, path in endpoints.items():
                await ANALYTICS_CACHE.clear()
                cold_started = perf_counter()
                cold_response = await client.get(path)
                cold_ms = (perf_counter() - cold_started) * 1000
                durations = []
                statuses = []
                for _ in range(samples):
                    started = perf_counter()
                    response = await client.get(path)
                    durations.append((perf_counter() - started) * 1000)
                    statuses.append(response.status_code)
                results[name] = {
                    "samples": samples,
                    "cold_ms": round(cold_ms, 2),
                    "cold_status": cold_response.status_code,
                    "p50_ms": round(_percentile(durations, 0.50), 2),
                    "p95_ms": round(_percentile(durations, 0.95), 2),
                    "p99_ms": round(_percentile(durations, 0.99), 2),
                    "throughput_rps": round(
                        samples / (sum(durations) / 1000), 3
                    ),
                    "error_rate": round(
                        sum(status >= 400 for status in statuses) / samples, 4
                    ),
                }

            panel_paths = (
                "/api/charities/stats",
                "/api/charities/grants/map",
                "/api/charities/grants/trends?months=24",
                "/api/charities/grants/themes",
                "/api/charities/grants/summary",
            )

            async def dashboard():
                responses = await asyncio.gather(
                    *(client.get(path) for path in panel_paths)
                )
                if any(response.status_code >= 400 for response in responses):
                    raise RuntimeError("Dashboard request failed")

            await ANALYTICS_CACHE.clear()
            cold_started = perf_counter()
            await dashboard()
            results["primary_dashboard_cold"] = {
                "elapsed_ms": round((perf_counter() - cold_started) * 1000, 2)
            }
            started = perf_counter()
            concurrent_results = await asyncio.gather(
                *(dashboard() for _ in range(concurrency)),
                return_exceptions=True,
            )
            elapsed = perf_counter() - started
            results["primary_dashboard_concurrent"] = {
                "concurrency": concurrency,
                "elapsed_ms": round(elapsed * 1000, 2),
                "throughput_rps": round(concurrency / elapsed, 3),
                "errors": sum(
                    isinstance(result, BaseException) for result in concurrent_results
                ),
            }
            results["analytics_cache"] = {
                "hits": ANALYTICS_CACHE.hits,
                "misses": ANALYTICS_CACHE.misses,
                "hit_ratio": round(ANALYTICS_CACHE.hit_ratio, 4),
            }
            pool = app.state.database.engine().sync_engine.pool
            results["database_pool"] = {
                "size": pool.size(),
                "checked_out": pool.checkedout(),
                "overflow": pool.overflow(),
            }
            return {"dataset_version": dataset_version, "results": results}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=5)
    arguments = parser.parse_args()
    if not 2 <= arguments.samples <= 100:
        parser.error("--samples must be between 2 and 100")
    if not 1 <= arguments.concurrency <= 20:
        parser.error("--concurrency must be between 1 and 20")
    print(
        json.dumps(
            asyncio.run(run(arguments.samples, arguments.concurrency)),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
