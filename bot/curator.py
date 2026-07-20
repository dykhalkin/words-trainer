"""Scheduled structured curator calls over deterministic analysis."""

from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any, Awaitable, Callable

from agents import Agent, RunConfig, Runner
from aiogram import Bot
from pydantic import BaseModel, ConfigDict, Field

from vocab import curator, db, llm, scheduler, words

from .config import Settings

logger = logging.getLogger(__name__)


class FocusItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    word_id: int
    reason: str = Field(min_length=1)


class CuratorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    focus: list[FocusItem] = Field(max_length=10)
    digest: str = Field(min_length=1, max_length=2000)


CURATOR_INSTRUCTIONS = """You are a vocabulary-learning curator.
Use only word IDs present in the supplied deterministic analysis.
Prioritize repeated errors and weak exercise types. Return a short Russian digest.
You cannot grade answers or modify progress.
"""


class CuratorService:
    def __init__(
        self,
        settings: Settings,
        runner: Callable[..., Awaitable[Any]] = Runner.run,
    ) -> None:
        self.settings = settings
        self.runner = runner
        if settings.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())

    async def run_for(
        self, database: db.Database, learner: dict[str, Any], *, kind: str = "plan"
    ) -> dict[str, Any] | None:
        if kind not in {"plan", "digest"}:
            raise ValueError("curator kind must be plan or digest")
        run_date = await curator.local_run_date(database, learner["id"])
        async with database.connection() as conn:
            existing = await db.fetch_one(
                conn,
                """SELECT * FROM curator_plans
                   WHERE user_id = %s AND run_date = %s AND kind = %s""",
                (learner["id"], run_date, kind),
            )
            if existing:
                return existing
            started = await conn.execute(
                """INSERT INTO curator_runs(user_id, kind, status)
                   VALUES (%s, %s, 'running') RETURNING id""",
                (learner["id"], kind),
            )
            run_id = (await started.fetchone())["id"]
        usage_id: int | None = None
        api_started = False
        try:
            if not self.settings.openai_api_key:
                raise RuntimeError("OpenAI is not configured")
            analysis = await curator.analyze(database, learner["id"])
            usage_id = await llm.reserve(
                database,
                learner["id"],
                "curator",
                Decimal(str(self.settings.llm_reservation_usd)),
            )
            agent = Agent(
                name="Vocabulary curator",
                instructions=CURATOR_INSTRUCTIONS,
                model=self.settings.curator_model,
                output_type=CuratorOutput,
            )
            api_started = True
            result = await self.runner(
                agent,
                json.dumps(analysis, ensure_ascii=False, default=str),
                max_turns=2,
                run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
            )
            output = result.final_output_as(CuratorOutput, raise_if_incorrect_type=True)
            allowed = {row["word_id"] for row in analysis["hard_words"]}
            output.focus = [item for item in output.focus if item.word_id in allowed]
            usage = result.context_wrapper.usage
            actual = (
                Decimal(usage.input_tokens)
                * Decimal(str(self.settings.llm_input_usd_per_million))
                + Decimal(usage.output_tokens)
                * Decimal(str(self.settings.llm_output_usd_per_million))
            ) / Decimal(1_000_000)
            await llm.reconcile(
                database,
                usage_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                actual_usd=actual,
            )
            plan = await curator.save_plan(database, learner["id"], kind, output.model_dump())
            async with database.connection() as conn:
                await conn.execute(
                    """UPDATE curator_runs SET status = 'succeeded', finished_at = now()
                       WHERE id = %s""",
                    (run_id,),
                )
            return plan
        except Exception as exc:
            if usage_id is not None and not api_started:
                await llm.release(database, usage_id)
            async with database.connection() as conn:
                await conn.execute(
                    """UPDATE curator_runs SET status = 'failed', error = %s, finished_at = now()
                       WHERE id = %s""",
                    (str(exc)[:1000], run_id),
                )
            logger.warning("curator unavailable for learner %s: %s", learner["id"], exc)
            return None


async def run_curator_cycle(
    service: CuratorService, bot: Bot, database: db.Database
) -> None:
    for learner in await words.list_users(database):
        await service.run_for(database, learner)
        run_date = await curator.local_run_date(database, learner["id"])
        if run_date.weekday() != 0:
            continue
        digest_plan = await service.run_for(database, learner, kind="digest")
        if not digest_plan:
            continue
        raw = digest_plan["plan"]
        payload = json.loads(raw) if isinstance(raw, str) else dict(raw)
        delivery = await scheduler.claim_digest(
            database, learner["id"], str(run_date), payload
        )
        if not delivery:
            continue
        try:
            message = await bot.send_message(
                learner["chat_id"], "Итоги недели:\n\n" + payload["digest"]
            )
        except Exception as exc:
            await scheduler.mark_delivery_failed(database, delivery["id"], str(exc))
        else:
            await scheduler.mark_delivery_sent(database, delivery["id"], message.message_id)
