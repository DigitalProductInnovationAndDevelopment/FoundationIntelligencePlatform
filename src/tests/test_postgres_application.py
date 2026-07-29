"""Application-journey parity tests for the PostgreSQL runtime."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bff.audit import AuditEvent
from bff.database import DatabaseSettings
from bff.postgres.analytics_repository import AnalyticsRepository
from bff.postgres.audit_repository import PostgresAuditSink
from bff.postgres.funder_repository import SourceFunderRepository
from bff.postgres.job_repository import PostgresJobRepository
from bff.postgres.organization_repository import OrganizationRepository
from bff.postgres.registry_repository import RegistryRepository
from bff.schemas import (
    CharityBase,
    CharityDetail,
    CharityStats,
    GrantListResponse,
    GrantMapResponse,
    GrantNetworkSummary,
    GrantThemesResponse,
    GrantTrendsResponse,
    RegistryDirectoryPage,
    RegistryOrganizationDetail,
    SankeyData,
    ScoreResponse,
    SourceFunderDetailResponse,
    SourceFunderListResponse,
)


def _route_contract(router) -> set[tuple[str, tuple[str, ...]]]:
    return {
        (route.path, tuple(sorted(route.methods)))
        for route in router.routes
        if isinstance(route, APIRoute)
    }


class TestPostgreSQLRouteParity(unittest.TestCase):
    def test_postgresql_router_covers_every_legacy_application_route(self):
        from bff.charity import router as legacy_router
        from bff.postgres.routes import router as postgresql_router

        self.assertEqual(_route_contract(legacy_router), _route_contract(postgresql_router))

    def test_production_startup_fails_without_postgresql_configuration(self):
        script = """
from fastapi.testclient import TestClient
from bff.main import app
with TestClient(app):
    pass
"""
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("DATABASE_")
        }
        environment.update(
            {
                "APP_ENV": "production",
                "AUTH_MODE": "oidc",
                "OIDC_ISSUER": "https://identity.example.invalid/",
                "OIDC_AUDIENCE": "foundation-intelligence-api",
                "OIDC_JWKS_JSON": '{"keys":[]}',
                "CORS_ORIGINS": "https://app.example.invalid",
                "DEV_AUTH_ENABLED": "false",
                "SESSION_COOKIE_SECURE": "true",
                "CORE_PROXY_ENABLED": "false",
                "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            }
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PostgreSQL connection settings are incomplete", result.stderr)


@unittest.skipUnless(
    os.getenv("RUN_POSTGRES_INTEGRATION") == "1" or os.getenv("TEST_DATABASE_URL"),
    "RUN_POSTGRES_INTEGRATION=1 or TEST_DATABASE_URL is required",
)
class TestPostgreSQLApplicationIntegration(unittest.TestCase):
    def test_all_read_journeys_against_real_postgresql(self):
        asyncio.run(self._exercise_reads())

    def test_mutations_are_transactional_and_audited_in_real_postgresql(self):
        asyncio.run(self._exercise_mutations())

    def test_production_startup_and_route_selection_against_real_postgresql(self):
        script = """
import sys
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from bff.main import app
with TestClient(app) as client:
    assert client.get('/health/ready').status_code == 200
    def descendants(routes):
        for route in routes:
            nested = getattr(route, 'routes', None)
            if nested is None and getattr(route, 'original_router', None) is not None:
                nested = route.original_router.routes
            if nested is not None:
                yield from descendants(nested)
            else:
                yield route
    routes = list(descendants(app.routes))
    endpoint_modules = {
        route.endpoint.__module__ for route in routes if isinstance(route, APIRoute)
    }
    assert 'bff.postgres.routes' in endpoint_modules, endpoint_modules
    assert 'bff.postgres.admin_routes' in endpoint_modules, endpoint_modules
    assert 'bff.charity' not in endpoint_modules
    assert 'bff.admin' not in endpoint_modules
    assert 'bff.charity' not in sys.modules
    assert 'bff.repositories' not in sys.modules
    assert 'sqlite3' not in sys.modules
"""
        environment = {
            **os.environ,
            "APP_ENV": "production",
            "AUTH_MODE": "oidc",
            "OIDC_ISSUER": "https://identity.example.invalid/",
            "OIDC_AUDIENCE": "foundation-intelligence-api",
            "OIDC_JWKS_JSON": '{"keys":[]}',
            "CORS_ORIGINS": "https://app.example.invalid",
            "DEV_AUTH_ENABLED": "false",
            "SESSION_COOKIE_SECURE": "true",
            "CORE_PROXY_ENABLED": "false",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        }
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parents[2],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def _database_url():
        return os.getenv("TEST_DATABASE_URL") or DatabaseSettings.from_env().sqlalchemy_url()

    async def _exercise_reads(self):
        engine = create_async_engine(self._database_url(), pool_pre_ping=True)
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        organizations = OrganizationRepository(sessions)
        registry = RegistryRepository(sessions)
        analytics = AnalyticsRepository(sessions)
        funders = SourceFunderRepository(sessions)
        try:
            async with sessions() as session:
                dataset_version = await session.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )
                organization_id = await session.scalar(
                    text(
                        """
                        SELECT charity_id FROM charities
                        WHERE dataset_version=:dataset_version
                        ORDER BY EXISTS (
                            SELECT 1 FROM grants WHERE grants.dataset_version=charities.dataset_version
                              AND (grants.funding_charity_id=charities.charity_id
                                   OR grants.recipient_charity_id=charities.charity_id)
                        ) DESC, charity_id LIMIT 1
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
                registry_id = await session.scalar(
                    text(
                        """
                        SELECT registry_id FROM charity_registry_organizations
                        WHERE dataset_version=:dataset_version AND is_current_source_record
                        ORDER BY registry_id LIMIT 1
                        """
                    ),
                    {"dataset_version": dataset_version},
                )
                funder_scope = (
                    await session.execute(
                        text(
                            """
                            SELECT country_code, source_funder_key
                            FROM grant_source_funder_facts
                            WHERE dataset_version=:dataset_version
                              AND country_code IS NOT NULL
                            GROUP BY country_code, source_funder_key
                            ORDER BY COUNT(*) DESC, country_code, source_funder_key LIMIT 1
                            """
                        ),
                        {"dataset_version": dataset_version},
                    )
                ).one()
                period = await session.scalar(
                    text(
                        """
                        SELECT to_char(award_date, 'YYYY-MM') FROM grant_overview_facts
                        WHERE dataset_version=:dataset_version AND award_date IS NOT NULL
                        ORDER BY award_date DESC LIMIT 1
                        """
                    ),
                    {"dataset_version": dataset_version},
                )

            listed = await organizations.list(limit=2)
            self.assertTrue(listed)
            for item in listed:
                CharityBase.model_validate(item)
            CharityStats.model_validate(await organizations.stats())
            CharityDetail.model_validate(await organizations.detail(int(organization_id)))
            GrantListResponse.model_validate(await organizations.grants(int(organization_id), "all"))
            SankeyData.model_validate(await organizations.sankey(int(organization_id)))
            ScoreResponse.model_validate(await organizations.score(int(organization_id), None))

            page = await registry.page(limit=2)
            RegistryDirectoryPage.model_validate(page)
            RegistryOrganizationDetail.model_validate(await registry.detail(str(registry_id)))

            self.assertIsInstance(await analytics.beneficiary_geographies(), list)
            GrantMapResponse.model_validate(await analytics.map())
            overview = await analytics.overview()
            self.assertIn("kpis", overview)
            self.assertIn("donors", await analytics.suggestions(sources=None, limit=5))
            GrantTrendsResponse.model_validate(await analytics.trends(months=24))
            GrantThemesResponse.model_validate(await analytics.themes())
            GrantNetworkSummary.model_validate(await analytics.summary())
            self.assertIn(
                "grants",
                await analytics.drilldown(
                    selection_type="period", selection_value=str(period)
                ),
            )

            country, funder_key = str(funder_scope[0]), str(funder_scope[1])
            SourceFunderListResponse.model_validate(
                await funders.list(beneficiary_country=country, page_size=5)
            )
            SourceFunderDetailResponse.model_validate(
                await funders.detail(funder_key, beneficiary_country=country)
            )
        finally:
            await engine.dispose()

    async def _exercise_mutations(self):
        engine = create_async_engine(self._database_url(), pool_pre_ping=True)
        connection = await engine.connect()
        outer_transaction = await connection.begin()
        sessions = async_sessionmaker(
            bind=connection,
            expire_on_commit=False,
            autoflush=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            async with sessions() as session:
                dataset_version = await session.scalar(
                    text("SELECT dataset_version FROM dataset_versions WHERE is_active")
                )
                identity = (
                    await session.execute(
                        text(
                            """
                            SELECT source_funder_key, charity_id
                            FROM grant_source_funder_facts
                            CROSS JOIN LATERAL (
                                SELECT charities.charity_id FROM charities
                                WHERE charities.dataset_version=:dataset_version
                                ORDER BY charities.charity_id LIMIT 1
                            ) AS profile
                            WHERE grant_source_funder_facts.dataset_version=:dataset_version
                            ORDER BY source_funder_key LIMIT 1
                            """
                        ),
                        {"dataset_version": dataset_version},
                    )
                ).one()

            key, profile_id = str(identity[0]), int(identity[1])
            unique = uuid.uuid4().hex
            funders = SourceFunderRepository(sessions)
            relinked = await funders.relink(key, profile_id, actor_id="phase5-integration")
            self.assertIsNotNone(relinked)
            queued = await funders.queue_profile_cache(
                key,
                actor_id="phase5-integration",
                idempotency_key=f"profile-{unique}",
            )
            self.assertEqual(queued["status"], "pending")
            self.assertEqual((await funders.profile_cache(key))["status"], "pending")
            self.assertIsNotNone(await funders.reset(key, actor_id="phase5-integration"))

            jobs = PostgresJobRepository(sessions)
            job = await jobs.enqueue(
                "quick_consolidate",
                {"integration_test": True},
                actor_id="phase5-integration",
                idempotency_key=f"pipeline-{unique}",
            )
            duplicate = await jobs.enqueue(
                "quick_consolidate",
                {"integration_test": True},
                actor_id="phase5-integration",
                idempotency_key=f"pipeline-{unique}",
            )
            self.assertEqual(job["job_id"], duplicate["job_id"])
            self.assertTrue(duplicate["idempotent_noop"])

            audit = PostgresAuditSink(sessions)
            await audit.record(
                AuditEvent(
                    actor_id="phase5-integration",
                    actor_role="administrator",
                    action="phase5.integration",
                    target="postgresql",
                    reason="transaction rollback test",
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    request_id=f"phase5-{unique}",
                    result="success",
                    http_status=200,
                    error_class=None,
                    dataset_version=str(dataset_version),
                )
            )
            async with sessions() as session:
                self.assertEqual(
                    await session.scalar(
                        text("SELECT COUNT(*) FROM audit_events WHERE request_id=:request_id"),
                        {"request_id": f"phase5-{unique}"},
                    ),
                    1,
                )
        finally:
            if outer_transaction.is_active:
                await outer_transaction.rollback()
            await connection.close()
            await engine.dispose()
