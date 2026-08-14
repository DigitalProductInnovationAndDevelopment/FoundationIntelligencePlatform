"""Pure durable-pipeline contracts shared by API, workers and local tests.

This module deliberately contains no AWS SDK calls. Production adapters can
publish the queue envelope to SQS and write the immutable object descriptor to
S3, while the same contracts remain fully testable without network access.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Protocol
from uuid import UUID


SOURCE_CONFIGURATION_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "source-pipelines.json"
)
LEGAL_STATES = frozenset({"approved", "unresolved", "restricted", "prohibited"})
OBJECT_ZONES = frozenset({"raw", "validated", "curated", "export"})
SOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class SourceConfiguration:
    source_name: str
    source_owner: str
    technical_owner: str
    legal_status: str
    licence_status: str
    terms_url: str | None
    rate_limit_per_minute: int
    user_agent: str
    freshness_sla_hours: int
    schedule_expression: str
    enabled: bool
    governance_blocked: bool
    last_success: str | None
    watermark: str | None
    classification: str
    retention_class: str
    schema_version: str
    credentials_reference: str | None
    retry_limit: int
    timeout_seconds: int
    maximum_pages: int
    maximum_records: int

    def validate(self) -> None:
        if not SOURCE_NAME_PATTERN.fullmatch(self.source_name):
            raise ValueError(f"Invalid source name: {self.source_name!r}")
        if self.legal_status not in LEGAL_STATES or self.licence_status not in LEGAL_STATES:
            raise ValueError(f"Invalid governance state for {self.source_name}")
        required_text = (
            self.source_owner,
            self.technical_owner,
            self.user_agent,
            self.schedule_expression,
            self.classification,
            self.retention_class,
            self.schema_version,
        )
        if any(not value.strip() for value in required_text):
            raise ValueError(f"Incomplete source configuration for {self.source_name}")
        if min(
            self.rate_limit_per_minute,
            self.freshness_sla_hours,
            self.timeout_seconds,
            self.maximum_pages,
            self.maximum_records,
        ) < 1:
            raise ValueError(f"Source limits must be positive for {self.source_name}")
        if not 0 <= self.retry_limit <= 20:
            raise ValueError(f"Retry limit is outside the allowed range for {self.source_name}")
        if self.enabled and (
            self.governance_blocked
            or self.legal_status != "approved"
            or self.licence_status != "approved"
        ):
            raise ValueError(f"Governance-blocked source cannot be scheduled: {self.source_name}")

    @property
    def configuration_checksum(self) -> str:
        return sha256_bytes(canonical_json(asdict(self)).encode("utf-8"))

    def database_record(self) -> dict[str, Any]:
        self.validate()
        result = asdict(self)
        result["configuration_checksum"] = self.configuration_checksum
        return result


def load_source_configurations(
    path: Path = SOURCE_CONFIGURATION_PATH,
) -> tuple[SourceConfiguration, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("configuration_version") != "1":
        raise ValueError("Unsupported source configuration version")
    sources = tuple(SourceConfiguration(**entry) for entry in payload.get("sources", []))
    if not sources:
        raise ValueError("At least one source configuration is required")
    names = [source.source_name for source in sources]
    if len(names) != len(set(names)):
        raise ValueError("Source names must be unique")
    for source in sources:
        source.validate()
    return sources


@dataclass(frozen=True)
class StorageObject:
    storage_object_id: UUID
    zone: str
    bucket_alias: str
    object_key: str
    object_version: str
    checksum: str
    content_length: int
    content_type: str
    source_name: str | None = None
    source_ingestion_run_id: UUID | None = None
    immutable: bool = True

    def validate(self) -> None:
        if self.zone not in OBJECT_ZONES:
            raise ValueError(f"Unsupported object zone: {self.zone}")
        if self.zone == "raw" and not self.immutable:
            raise ValueError("Raw source objects must be immutable")
        if not re.fullmatch(r"[a-f0-9]{64}", self.checksum):
            raise ValueError("Object checksum must be lowercase SHA-256")
        if self.content_length < 0:
            raise ValueError("Object length cannot be negative")
        if not all(
            value.strip()
            for value in (self.bucket_alias, self.object_key, self.object_version, self.content_type)
        ):
            raise ValueError("Object location and content type are required")


def object_key(
    *, source_name: str, run_id: UUID, zone: str, checksum: str, extension: str
) -> str:
    if not SOURCE_NAME_PATTERN.fullmatch(source_name):
        raise ValueError("Invalid source name")
    if zone not in OBJECT_ZONES:
        raise ValueError("Invalid object zone")
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise ValueError("Invalid object checksum")
    suffix = extension.strip().lower().lstrip(".")
    if not re.fullmatch(r"[a-z0-9]{1,12}", suffix):
        raise ValueError("Invalid object extension")
    return f"{zone}/{source_name}/{run_id}/{checksum}.{suffix}"


@dataclass(frozen=True)
class IngestionManifest:
    source_ingestion_run_id: UUID
    schema_version: str
    source_name: str
    source_version: str | None
    dataset_version: str
    raw_object_id: UUID | None
    validated_object_id: UUID | None
    curated_object_id: UUID | None
    watermark_before: str | None
    watermark_after: str | None
    record_count: int
    accepted_count: int
    rejected_count: int
    quarantined_count: int
    retry_count: int
    generated_at: str

    @classmethod
    def create(cls, **values: Any) -> "IngestionManifest":
        values.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
        manifest = cls(**values)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not SOURCE_NAME_PATTERN.fullmatch(self.source_name):
            raise ValueError("Invalid manifest source")
        counts = (
            self.record_count,
            self.accepted_count,
            self.rejected_count,
            self.quarantined_count,
            self.retry_count,
        )
        if min(counts) < 0:
            raise ValueError("Manifest counts cannot be negative")
        classified = self.accepted_count + self.rejected_count + self.quarantined_count
        if classified > self.record_count:
            raise ValueError("Manifest result counts exceed the source record count")
        if not self.schema_version.strip() or not self.dataset_version.strip():
            raise ValueError("Manifest schema and dataset versions are required")

    @property
    def payload(self) -> dict[str, Any]:
        self.validate()
        result = asdict(self)
        for key, value in tuple(result.items()):
            if isinstance(value, UUID):
                result[key] = str(value)
        return result

    @property
    def checksum(self) -> str:
        return sha256_bytes(canonical_json(self.payload).encode("utf-8"))


@dataclass(frozen=True)
class QueueEnvelope:
    schema_version: str
    job_id: UUID
    job_type: str
    queue_name: str
    requested_at: str
    attempt: int
    max_attempts: int

    def validate(self) -> None:
        if self.schema_version != "job-envelope-v1":
            raise ValueError("Unsupported queue envelope version")
        if not self.job_type.strip() or not self.queue_name.strip():
            raise ValueError("Queue and job type are required")
        if self.attempt < 1 or self.max_attempts < self.attempt:
            raise ValueError("Invalid queue attempt")

    @property
    def payload(self) -> dict[str, Any]:
        self.validate()
        result = asdict(self)
        result["job_id"] = str(self.job_id)
        return result


class ObjectStore(Protocol):
    """Minimal immutable object-store boundary implemented by an S3 adapter."""

    async def put_if_absent(self, descriptor: StorageObject, payload: bytes) -> None: ...

    async def get(self, descriptor: StorageObject) -> bytes: ...


class InMemoryObjectStore:
    """Deterministic local substitute; never used as production coordination."""

    def __init__(self) -> None:
        self._objects: dict[tuple[str, str, str, str], tuple[StorageObject, bytes]] = {}

    @staticmethod
    def _identity(descriptor: StorageObject) -> tuple[str, str, str, str]:
        return (
            descriptor.zone,
            descriptor.bucket_alias,
            descriptor.object_key,
            descriptor.object_version,
        )

    async def put_if_absent(self, descriptor: StorageObject, payload: bytes) -> None:
        descriptor.validate()
        if descriptor.content_length != len(payload):
            raise ValueError("Object length does not match payload")
        if descriptor.checksum != sha256_bytes(payload):
            raise ValueError("Object checksum does not match payload")
        identity = self._identity(descriptor)
        existing = self._objects.get(identity)
        if existing:
            if existing[1] != payload:
                raise ValueError("Immutable object identity already contains different bytes")
            return
        self._objects[identity] = (descriptor, bytes(payload))

    async def get(self, descriptor: StorageObject) -> bytes:
        return bytes(self._objects[self._identity(descriptor)][1])


def require_sources(
    configurations: Iterable[SourceConfiguration], required: Iterable[str]
) -> None:
    names = {source.source_name for source in configurations}
    missing = sorted(set(required) - names)
    if missing:
        raise ValueError(f"Missing source configurations: {', '.join(missing)}")


def redacted_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(configuration)
    if result.get("credentials_reference"):
        result["credentials_reference"] = "configured-reference"
    return result
