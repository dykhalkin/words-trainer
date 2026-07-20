"""Bot-owned execution adapter for persistent background jobs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from aiogram import Bot

from vocab import db, jobs, scheduler

from .curator import CuratorService, run_digest_cycle, run_plan_cycle
from .push import run_push_cycle

logger = logging.getLogger(__name__)
Job = Callable[[], Awaitable[Any]]


class BotJobRunner:
    def __init__(
        self, database: db.Database, bot: Bot, curator: CuratorService
    ) -> None:
        self.database = database
        self.registry: dict[str, Job] = {
            "push": lambda: run_push_cycle(bot, database),
            "curator_plan": lambda: run_plan_cycle(curator, database),
            "weekly_digest": lambda: run_digest_cycle(curator, bot, database),
            "task_sweep": lambda: scheduler.sweep_expired(database),
            "session_cleanup": lambda: scheduler.close_idle_sessions(database),
        }

    async def _execute(self, row: dict[str, Any]) -> None:
        try:
            value = await self.registry[row["job_name"]]()
        except Exception as exc:
            logger.exception("background job failed", extra={"job_run_id": row["id"]})
            await jobs.finish_run(
                self.database, row["id"], status="failed", error=str(exc)
            )
        else:
            await jobs.finish_run(
                self.database, row["id"], status="succeeded", result=value
            )

    async def scheduled(self, job_name: str, period_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        period = int(now.timestamp()) // period_seconds
        row = await jobs.begin_scheduled_run(
            self.database, job_name, f"scheduled:{job_name}:{period}"
        )
        if row and row["status"] == "running":
            await self._execute(row)

    async def drain_manual(self) -> None:
        while row := await jobs.claim_queued(self.database):
            await self._execute(row)
