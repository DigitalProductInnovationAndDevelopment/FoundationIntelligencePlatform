"""Long-running ECS/local entrypoint for PostgreSQL-backed pipeline jobs."""

from __future__ import annotations

import asyncio
import os
import signal
import socket

from sqlalchemy import text

from bff.database import DatabaseAccess
from bff.postgres.job_repository import PostgresJobRepository
from bff.utils.logging import logger
from observability.metrics import load_observability_configuration
from pipelines.durable_worker import DurableWorker
from pipelines.worker_handlers import build_handlers


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


async def run() -> None:
    if os.getenv("DATA_RUNTIME_MODE", "").strip().lower() != "postgresql":
        raise RuntimeError("The production worker requires DATA_RUNTIME_MODE=postgresql")
    database = DatabaseAccess.from_env()
    if not database.writer_configured:
        raise RuntimeError("The production worker requires the restricted runtime writer")
    if not database.pipeline_configured:
        raise RuntimeError("The production worker requires the dedicated dataset publisher")
    sessions = database.write_sessions()
    pipeline_sessions = database.pipeline_sessions()
    expected_schema = load_observability_configuration().expected_schema_version
    async with pipeline_sessions() as session:
        actual_schema = await session.scalar(text("SELECT version_num FROM alembic_version"))
    if actual_schema != expected_schema:
        await database.close()
        raise RuntimeError(
            f"Worker schema gate failed: expected {expected_schema}, received {actual_schema}"
        )

    worker_id = f"{socket.gethostname()}-{os.getpid()}"
    queue_name = os.getenv("WORKER_QUEUE_NAME", "pipeline").strip() or "pipeline"
    lease_seconds = int(_bounded_float("WORKER_LEASE_SECONDS", 90, 15, 3600))
    idle_seconds = _bounded_float("WORKER_IDLE_SECONDS", 2, 0.25, 60)
    repository = PostgresJobRepository(sessions)
    handlers = build_handlers(pipeline_sessions)
    worker = DurableWorker(
        repository,
        handlers.mapping,
        worker_id=worker_id,
        queue_name=queue_name,
        lease_seconds=lease_seconds,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for handled_signal in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(handled_signal, stop.set)
    logger.info(
        "pipeline_worker_started",
        extra={"worker_id": worker_id, "queue": queue_name},
    )
    try:
        while not stop.is_set():
            expired = await repository.fail_expired(queue_name=queue_name)
            if expired["failed"]:
                logger.warning(
                    "stale_jobs_failed",
                    extra={"worker_id": worker_id, "count": expired["failed"]},
                )
            result = await worker.run_once()
            if result.status == "idle":
                try:
                    await asyncio.wait_for(stop.wait(), timeout=idle_seconds)
                except asyncio.TimeoutError:
                    pass
    finally:
        logger.info("pipeline_worker_stopped", extra={"worker_id": worker_id})
        await database.close()


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
