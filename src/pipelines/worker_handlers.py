"""Production handlers for durable PostgreSQL pipeline jobs."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import shutil
from types import SimpleNamespace
from typing import Any, Mapping, Protocol
from urllib.parse import urlparse
import sqlite3
import tempfile

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from bff.database import DatabaseSettings
from bff.postgres.funder_repository import SourceFunderRepository
from bff.postgres.registry_repository import RegistryRepository
from migration.sqlite_to_postgres import migrate
from pipelines.run_pipeline import run_pipeline


class WorkerConfigurationError(RuntimeError):
    """Raised before a job mutates data when worker configuration is incomplete."""


PIPELINE_ARTIFACT_LOCK_ID = 2_083_370_803_872


class PipelineArtifactStore(Protocol):
    def download_baseline(self, destination: Path) -> str: ...

    def publish_snapshot(self, source: Path, *, checksum: str, job_id: str) -> None: ...


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _s3_location(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path.lstrip("/"):
        raise WorkerConfigurationError("Pipeline artifact URI must be a bounded s3:// URI")
    return parsed.netloc, parsed.path.lstrip("/")


@dataclass(frozen=True)
class S3PipelineArtifactStore:
    current_uri: str
    fallback_uri: str
    fallback_checksum: str

    def _client(self) -> Any:
        import boto3

        return boto3.client("s3")

    def download_baseline(self, destination: Path) -> str:
        client = self._client()
        current_bucket, current_key = _s3_location(self.current_uri)
        fallback_bucket, fallback_key = _s3_location(self.fallback_uri)
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        expected_checksum: str | None = None
        try:
            metadata = client.head_object(Bucket=current_bucket, Key=current_key)
            expected_checksum = str(metadata.get("Metadata", {}).get("sha256") or "")
            client.download_file(current_bucket, current_key, str(destination))
        except Exception as exc:
            from botocore.exceptions import ClientError

            if not isinstance(exc, ClientError):
                raise
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"404", "NoSuchKey", "NotFound"}:
                raise
            client.download_file(fallback_bucket, fallback_key, str(destination))
            expected_checksum = self.fallback_checksum
        actual_checksum = sha256_file(destination)
        if not expected_checksum or actual_checksum != expected_checksum:
            destination.unlink(missing_ok=True)
            raise WorkerConfigurationError("Pipeline baseline checksum verification failed")
        return actual_checksum

    def publish_snapshot(self, source: Path, *, checksum: str, job_id: str) -> None:
        bucket, key = _s3_location(self.current_uri)
        self._client().upload_file(
            str(source),
            bucket,
            key,
            ExtraArgs={
                "ServerSideEncryption": "AES256",
                "Metadata": {"sha256": checksum, "job-id": job_id},
            },
        )


@dataclass(frozen=True)
class LocalPipelineArtifactStore:
    """Checksum-verified local snapshot store used only by Docker development."""

    current_path: Path
    fallback_path: Path
    fallback_checksum: str

    @property
    def checksum_path(self) -> Path:
        return self.current_path.with_suffix(self.current_path.suffix + ".sha256")

    def download_baseline(self, destination: Path) -> str:
        source = self.current_path if self.current_path.is_file() else self.fallback_path
        if not source.is_file():
            raise WorkerConfigurationError("Local pipeline baseline is unavailable")
        expected = (
            self.checksum_path.read_text(encoding="utf-8").strip()
            if source == self.current_path and self.checksum_path.is_file()
            else self.fallback_checksum
        )
        if len(expected) != 64:
            raise WorkerConfigurationError("Local pipeline baseline checksum is unavailable")
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copyfile(source, destination)
        actual = sha256_file(destination)
        if actual != expected:
            destination.unlink(missing_ok=True)
            raise WorkerConfigurationError("Local pipeline baseline checksum verification failed")
        return actual

    def publish_snapshot(self, source: Path, *, checksum: str, job_id: str) -> None:
        del job_id
        self.current_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.current_path.with_suffix(self.current_path.suffix + ".tmp")
        checksum_temporary = self.checksum_path.with_suffix(
            self.checksum_path.suffix + ".tmp"
        )
        shutil.copyfile(source, temporary)
        checksum_temporary.write_text(checksum + "\n", encoding="utf-8")
        os.replace(temporary, self.current_path)
        os.replace(checksum_temporary, self.checksum_path)


def _clear_snapshot_runtime_state(path: Path) -> None:
    """Keep interactive global state authoritative in RDS, not in snapshots."""
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM source_funder_link_overrides")
        connection.execute("DELETE FROM source_funder_profile_cache")
        connection.commit()
    finally:
        connection.close()


class WorkerHandlers:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        *,
        pipeline_settings: DatabaseSettings | None,
        artifact_store: PipelineArtifactStore | None,
        source_schema_version: str | None,
        code_revision: str | None,
    ) -> None:
        self.sessions = sessions
        self.pipeline_settings = pipeline_settings
        self.artifact_store = artifact_store
        self.source_schema_version = str(source_schema_version or "").strip()
        self.code_revision = str(code_revision or "").strip()

    @property
    def mapping(self):
        return {
            "source_funder_profile_hydration": self.source_funder_profile_hydration,
            "source_funder_enrichment": self.source_funder_enrichment,
            "registry_enrichment": self.registry_enrichment,
            "full_run": self.full_run,
        }

    @asynccontextmanager
    async def _artifact_lock(self):
        """Serialize mutable snapshot work across overlapping ECS deployments."""
        async with self.sessions() as session, session.begin():
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": PIPELINE_ARTIFACT_LOCK_ID},
            )
            yield

    async def _persist_snapshot_profile(self, profile_ids: list[int], job_id: str) -> None:
        if self.artifact_store is None:
            raise WorkerConfigurationError("Pipeline snapshot configuration is missing")
        with tempfile.TemporaryDirectory(prefix="profile-snapshot-", dir=os.getenv("TMPDIR")) as temporary:
            snapshot = Path(temporary) / "charities.db"
            await asyncio.to_thread(self.artifact_store.download_baseline, snapshot)
            from bff.charity import _fast_link_confirmed_profiles

            profiles = await asyncio.to_thread(
                _fast_link_confirmed_profiles, profile_ids, str(snapshot)
            )
            if len(profiles) != len(profile_ids):
                raise RuntimeError("Snapshot profile mutation was incomplete")
            checksum = await asyncio.to_thread(sha256_file, snapshot)
            await asyncio.to_thread(
                self.artifact_store.publish_snapshot,
                snapshot,
                checksum=checksum,
                job_id=job_id,
            )

    async def _infer_source_targets(
        self, reg_numbers: list[int]
    ) -> list[tuple[str, int]]:
        identifiers = [f"GB-CHC-{number}" for number in reg_numbers]
        async with self.sessions() as session:
            dataset_version = await session.scalar(
                text("SELECT dataset_version FROM dataset_versions WHERE is_active")
            )
            rows = (
                await session.execute(
                    text(
                        """
                        SELECT DISTINCT source_funder_key, source_organization_id
                        FROM grant_source_funder_facts
                        WHERE dataset_version=:dataset_version
                          AND source_organization_id=ANY(CAST(:identifiers AS text[]))
                        ORDER BY source_funder_key
                        """
                    ),
                    {"dataset_version": dataset_version, "identifiers": identifiers},
                )
            ).mappings().all()
        return [
            (str(row["source_funder_key"]), int(str(row["source_organization_id"]).rsplit("-", 1)[-1]))
            for row in rows
        ]

    async def source_funder_enrichment(
        self, job: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        payload = dict(job.get("input") or {})
        targets = [
            (str(item.get("source_funder_key") or "").strip(), int(item.get("profile_id") or 0))
            for item in payload.get("targets") or []
        ]
        targets = [(key, profile_id) for key, profile_id in targets if key and profile_id > 0]
        if not targets:
            numbers = sorted({int(value) for value in payload.get("reg_numbers") or []})
            targets = await self._infer_source_targets(numbers)
        if not targets:
            raise ValueError("No active-dataset source funder matches the confirmed profile")
        if len(targets) > 5:
            raise ValueError("A source-funder enrichment job may contain at most five targets")
        repository = SourceFunderRepository(self.sessions)
        registry = RegistryRepository(self.sessions)
        actor_id = str(job.get("requested_by") or "pipeline-worker")
        profiles: list[dict[str, Any]] = []
        async with self._artifact_lock():
            await self._persist_snapshot_profile(
                sorted({profile_id for _, profile_id in targets}), str(job["job_id"])
            )
            for source_funder_key, profile_id in targets:
                exact_profile = await registry.link_exact_profile(
                    profile_id, actor_id=actor_id
                )
                if exact_profile is None:
                    raise ValueError(
                        "No cached Charity Commission record matches the confirmed profile"
                    )
                linked = await repository.relink(
                    source_funder_key, profile_id, actor_id=actor_id
                )
                if linked is None:
                    raise ValueError("The source-funder target no longer exists")
                async with self.sessions() as verification_session:
                    effective = await repository._effective_profile(
                        verification_session, source_funder_key
                    )
                if effective is None or int(effective["profile_id"]) != profile_id:
                    raise RuntimeError("Source-funder profile mutation was not persisted")
                profiles.append({**dict(linked), **exact_profile})
        return {
            "record_count": len(profiles),
            "accepted_count": len(profiles),
            "profiles": profiles,
        }

    async def source_funder_profile_hydration(
        self, job: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        source_funder_key = str(
            (job.get("input") or {}).get("source_funder_key") or ""
        ).strip()
        if not source_funder_key:
            raise ValueError("Profile hydration requires a source funder key")
        hydrated = await SourceFunderRepository(self.sessions).hydrate_profile_cache(
            source_funder_key,
            job_id=str(job["job_id"]),
        )
        return {
            "record_count": 1,
            "accepted_count": 1,
            "source_funder_key": hydrated["source_funder_key"],
            "profile_id": hydrated["profile_id"],
        }

    async def registry_enrichment(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        numbers = sorted({int(value) for value in (job.get("input") or {}).get("reg_numbers") or []})
        if len(numbers) != 1:
            raise ValueError("Registry enrichment requires exactly one charity number")
        async with self._artifact_lock():
            await self._persist_snapshot_profile(numbers, str(job["job_id"]))
            linked = await RegistryRepository(self.sessions).link_exact_profile(
                numbers[0], actor_id=str(job.get("requested_by") or "pipeline-worker")
            )
        if linked is None:
            raise ValueError("No exact active-dataset registry/profile pair exists")
        return {"record_count": 1, "accepted_count": 1, "profiles": [linked]}

    async def full_run(self, job: Mapping[str, Any]) -> Mapping[str, Any]:
        if self.pipeline_settings is None or self.artifact_store is None:
            raise WorkerConfigurationError("Full pipeline publisher configuration is missing")
        if len(self.code_revision) != 40 or any(
            character not in "0123456789abcdef" for character in self.code_revision
        ):
            raise WorkerConfigurationError("CODE_REVISION must be a full lowercase Git SHA")
        if not self.source_schema_version:
            raise WorkerConfigurationError("PIPELINE_SOURCE_SCHEMA_VERSION is required")
        payload = dict(job.get("input") or {})
        if payload.get("fresh"):
            raise WorkerConfigurationError(
                "Fresh full runs are disabled for the versioned RDS publisher"
            )
        job_id = str(job["job_id"])
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dataset_version = f"worker-{timestamp}-{job_id}"
        async with self._artifact_lock():
            with tempfile.TemporaryDirectory(prefix="pipeline-", dir=os.getenv("TMPDIR")) as temporary:
                work_root = Path(temporary)
                database_path = work_root / "data" / "charities.db"
                await asyncio.to_thread(self.artifact_store.download_baseline, database_path)
                arguments = SimpleNamespace(
                    source="full_run",
                    work_directory=str(work_root),
                    raw_cc_output=None,
                    raw_ts_output=None,
                    reg_numbers=payload.get("reg_numbers"),
                    search=payload.get("search_term"),
                    org_ids=None,
                    all_orgs=False,
                    limit=payload.get("limit"),
                    sleep=1.0,
                    timeout=10.0,
                    skip_scrape=False,
                    skip_contact_crawler=bool(
                        payload.get("skip_contact_crawler", False)
                    ),
                    fresh=False,
                )
                pipeline_result = await asyncio.to_thread(run_pipeline, arguments)
                imported = int((pipeline_result or {}).get("successful_primary_imports") or 0)
                if imported < 1:
                    raise RuntimeError("Full pipeline completed without a primary-source mutation")
                await asyncio.to_thread(_clear_snapshot_runtime_state, database_path)
                checksum = await asyncio.to_thread(sha256_file, database_path)
                await asyncio.to_thread(
                    self.artifact_store.publish_snapshot,
                    database_path,
                    checksum=checksum,
                    job_id=job_id,
                )
                report = await migrate(
                    database_path,
                    checksum,
                    self.source_schema_version,
                    dataset_version,
                    self.code_revision,
                    str(job.get("requested_by") or "pipeline-worker"),
                    "service",
                    work_root / "evidence",
                    enforce_baseline=False,
                    remote_postgres=True,
                    database_settings=self.pipeline_settings,
                )
        return {
            "dataset_version": dataset_version,
            "record_count": int(report["target_counts"].get("grants", 0)),
            "accepted_count": imported,
            "activation_status": report["activation_status"],
            "source_database_checksum": checksum,
            "retargeted_overrides": int(report.get("retargeted_overrides") or 0),
        }


def build_handlers(
    sessions: async_sessionmaker[AsyncSession],
    environment: Mapping[str, str] | None = None,
) -> WorkerHandlers:
    env = os.environ if environment is None else environment
    current_uri = str(env.get("PIPELINE_SNAPSHOT_S3_URI") or "").strip()
    fallback_uri = str(env.get("PIPELINE_FALLBACK_S3_URI") or "").strip()
    fallback_checksum = str(env.get("PIPELINE_FALLBACK_SHA256") or "").strip()
    artifact_store: PipelineArtifactStore | None = None
    local_current = str(env.get("PIPELINE_SNAPSHOT_PATH") or "").strip()
    local_fallback = str(env.get("PIPELINE_BASELINE_PATH") or "").strip()
    if local_current and local_fallback and fallback_checksum:
        artifact_store = LocalPipelineArtifactStore(
            current_path=Path(local_current),
            fallback_path=Path(local_fallback),
            fallback_checksum=fallback_checksum,
        )
    elif current_uri and fallback_uri and fallback_checksum:
        artifact_store = S3PipelineArtifactStore(
            current_uri=current_uri,
            fallback_uri=fallback_uri,
            fallback_checksum=fallback_checksum,
        )
    return WorkerHandlers(
        sessions,
        pipeline_settings=DatabaseSettings.pipeline_from_env(env),
        artifact_store=artifact_store,
        source_schema_version=env.get("PIPELINE_SOURCE_SCHEMA_VERSION"),
        code_revision=env.get("CODE_REVISION"),
    )
