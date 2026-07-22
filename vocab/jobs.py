"""Persistent controls and run records for bot-owned background jobs."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.types.json import Jsonb

from . import db

JOB_NAMES = (
    "push",
    "curator_plan",
    "reminder_refresh",
    "weekly_digest",
    "task_sweep",
    "session_cleanup",
)


def validate_job_name(job_name: str) -> str:
    if job_name not in JOB_NAMES:
        raise ValueError(f"unknown job: {job_name}")
    return job_name


async def ensure_controls(database: db.Database) -> None:
    async with database.connection() as conn:
        for name in JOB_NAMES:
            await conn.execute(
                """INSERT INTO job_controls(job_name) VALUES (%s)
                   ON CONFLICT(job_name) DO NOTHING""",
                (name,),
            )


async def list_jobs(database: db.Database) -> list[dict[str, Any]]:
    await ensure_controls(database)
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT c.job_name, c.enabled, c.updated_at,
                      r.id AS last_run_id, r.trigger AS last_trigger,
                      r.status AS last_status, r.requested_at AS last_requested_at,
                      r.finished_at AS last_finished_at, r.error AS last_error
               FROM job_controls c
               LEFT JOIN LATERAL (
                   SELECT * FROM job_runs jr WHERE jr.job_name = c.job_name
                   ORDER BY jr.requested_at DESC, jr.id DESC LIMIT 1
               ) r ON true
               ORDER BY c.job_name""",
        )


async def set_enabled(
    database: db.Database, job_name: str, enabled: bool
) -> dict[str, Any]:
    validate_job_name(job_name)
    await ensure_controls(database)
    async with database.connection() as conn:
        result = await conn.execute(
            """UPDATE job_controls SET enabled = %s, updated_at = now()
               WHERE job_name = %s RETURNING *""",
            (enabled, job_name),
        )
        return await result.fetchone()


async def enqueue_run(
    database: db.Database, job_name: str, *, force: bool = False
) -> dict[str, Any]:
    validate_job_name(job_name)
    await ensure_controls(database)
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "SELECT * FROM job_controls WHERE job_name = %s FOR UPDATE",
                (job_name,),
            )
            control = await result.fetchone()
            if not control["enabled"] and not force:
                raise ValueError("job is disabled; use --force to enqueue it")
            key = f"manual:{job_name}:{uuid.uuid4().hex}"
            inserted = await conn.execute(
                """INSERT INTO job_runs(
                       job_name, trigger, status, force_run, idempotency_key
                   ) VALUES (%s, 'manual', 'queued', %s, %s) RETURNING *""",
                (job_name, force, key),
            )
            return await inserted.fetchone()


async def list_runs(
    database: db.Database,
    *,
    job_name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    name_clause = " WHERE job_name = %s" if job_name else ""
    params: tuple[Any, ...] = (job_name, limit) if job_name else (limit,)
    if job_name:
        validate_job_name(job_name)
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            "SELECT * FROM job_runs"
            + name_clause
            + " ORDER BY requested_at DESC, id DESC LIMIT %s",
            params,
        )


async def begin_scheduled_run(
    database: db.Database, job_name: str, idempotency_key: str
) -> dict[str, Any] | None:
    validate_job_name(job_name)
    await ensure_controls(database)
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                "SELECT enabled FROM job_controls WHERE job_name = %s FOR UPDATE",
                (job_name,),
            )
            control = await result.fetchone()
            status = "running" if control["enabled"] else "skipped"
            inserted = await conn.execute(
                """INSERT INTO job_runs(
                       job_name, trigger, status, idempotency_key, started_at, finished_at
                   ) VALUES (%s, 'scheduled', %s, %s,
                             CASE WHEN %s = 'running' THEN now() END,
                             CASE WHEN %s = 'skipped' THEN now() END)
                   ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
                (job_name, status, idempotency_key, status, status),
            )
            return await inserted.fetchone()


async def claim_queued(database: db.Database) -> dict[str, Any] | None:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """SELECT r.* FROM job_runs r
                   WHERE r.status = 'queued'
                   ORDER BY r.requested_at, r.id
                   FOR UPDATE OF r SKIP LOCKED LIMIT 1"""
            )
            row = await result.fetchone()
            if not row:
                return None
            updated = await conn.execute(
                """UPDATE job_runs SET status = 'running', started_at = now()
                   WHERE id = %s AND status = 'queued' RETURNING *""",
                (row["id"],),
            )
            return await updated.fetchone()


async def finish_run(
    database: db.Database,
    run_id: int,
    *,
    status: str,
    result: Any = None,
    error: str | None = None,
) -> None:
    if status not in {"succeeded", "failed"}:
        raise ValueError("terminal job status must be succeeded or failed")
    payload = result if isinstance(result, dict) else {"value": result}
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE job_runs SET status = %s, result = %s, error = %s,
                      finished_at = now()
               WHERE id = %s AND status = 'running'""",
            (status, Jsonb(payload), error[:1000] if error else None, run_id),
        )


async def recover_interrupted(database: db.Database) -> int:
    """Close attempts abandoned by a previous bot process.

    Queued manual requests are deliberately left untouched and will be claimed
    after restart. A running attempt is not replayed because delivery jobs have
    their own idempotency boundary and an operator can explicitly enqueue again.
    """
    async with database.connection() as conn:
        result = await conn.execute(
            """UPDATE job_runs SET status = 'failed', finished_at = now(),
                      error = 'bot process stopped before the run completed'
               WHERE status = 'running'"""
        )
        return result.rowcount
