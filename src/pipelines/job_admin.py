"""Bounded one-off backlog inspection/retirement for worker deployments."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

import asyncpg


RETIREMENT_REASON = (
    "Job was created before the worker execution service existed and was retired "
    "during worker deployment."
)


async def _connection() -> asyncpg.Connection:
    required = {
        name: str(os.getenv(name) or "").strip()
        for name in (
            "DATABASE_HOST",
            "DATABASE_PORT",
            "DATABASE_NAME",
            "DATABASE_ADMIN_USER",
            "DATABASE_ADMIN_PASSWORD",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError("Missing database settings: " + ", ".join(missing))
    return await asyncpg.connect(
        host=required["DATABASE_HOST"],
        port=int(required["DATABASE_PORT"]),
        database=required["DATABASE_NAME"],
        user=required["DATABASE_ADMIN_USER"],
        password=required["DATABASE_ADMIN_PASSWORD"],
        ssl=str(os.getenv("DATABASE_SSL_MODE") or "require"),
        command_timeout=30,
    )


async def inspect_backlog() -> dict[str, object]:
    connection = await _connection()
    try:
        summary = await connection.fetch(
            """
            SELECT job_type, status, COUNT(*) AS count
            FROM job_runs
            GROUP BY job_type, status
            ORDER BY job_type, status
            """
        )
        active = await connection.fetch(
            """
            SELECT job_run_id, job_type, status, requested_at, started_at,
                   heartbeat_at, lease_expires_at
            FROM job_runs
            WHERE status IN ('queued', 'running')
            ORDER BY requested_at, job_run_id
            LIMIT 200
            """
        )
        return {
            "summary": [dict(row) for row in summary],
            "active": [dict(row) for row in active],
            "active_truncated": len(active) == 200,
        }
    finally:
        await connection.close()


async def retire_jobs(job_ids: list[uuid.UUID]) -> dict[str, object]:
    if not job_ids or len(job_ids) > 200:
        raise ValueError("Retirement requires between one and 200 explicit job IDs")
    connection = await _connection()
    try:
        async with connection.transaction():
            rows = await connection.fetch(
                """
                UPDATE job_runs
                SET status='cancelled',
                    started_at=COALESCE(started_at, requested_at),
                    completed_at=CURRENT_TIMESTAMP,
                    heartbeat_at=NULL, lease_expires_at=NULL,
                    error_class='PreWorkerDeploymentRetirement',
                    error_message=$2, failure_reason=$2
                WHERE job_run_id=ANY($1::uuid[])
                  AND status IN ('queued', 'running')
                RETURNING job_run_id, job_type, status
                """,
                job_ids,
                RETIREMENT_REASON,
            )
            retired = {row["job_run_id"] for row in rows}
            for row in rows:
                await connection.execute(
                    """
                    INSERT INTO job_events (
                        job_event_id, job_run_id, sequence, event_type,
                        actor_id, details
                    ) VALUES (
                        $1, $2,
                        (SELECT COALESCE(MAX(sequence), 0) + 1
                         FROM job_events WHERE job_run_id=$2),
                        'cancelled', 'worker-deployment', $3::jsonb
                    )
                    """,
                    uuid.uuid4(),
                    row["job_run_id"],
                    json.dumps({"reason": RETIREMENT_REASON}),
                )
            await connection.execute(
                """
                UPDATE worker_heartbeats
                SET status='stopped', job_run_id=NULL,
                    heartbeat_at=CURRENT_TIMESTAMP
                WHERE job_run_id=ANY($1::uuid[])
                """,
                list(retired),
            )
        return {
            "requested": [str(job_id) for job_id in job_ids],
            "retired": [str(row["job_run_id"]) for row in rows],
            "retired_count": len(rows),
        }
    finally:
        await connection.close()


async def run(arguments: argparse.Namespace) -> dict[str, object]:
    if arguments.action == "inspect":
        return await inspect_backlog()
    return await retire_jobs([uuid.UUID(value) for value in arguments.job_id])


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or retire explicit durable jobs")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("inspect")
    retire = subparsers.add_parser("retire")
    retire.add_argument("--job-id", action="append", required=True)
    result = asyncio.run(run(parser.parse_args()))
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
