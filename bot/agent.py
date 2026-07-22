"""Learner-scoped OpenAI tutor with staged-only writes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from agents import Agent, RunConfig, RunContextWrapper, Runner, function_tool
from pydantic import BaseModel, ConfigDict, Field, model_validator

from vocab import curator, db, llm, scheduler, words
from vocab.words import WordCard

from .config import Settings

INSTRUCTIONS = """You are a concise, friendly German tutor speaking Russian.
Use tools for learner facts; never invent progress or override deterministic grading.
When useful vocabulary emerges, call propose_words with complete validated cards.
Proposals are previews only and require learner confirmation. Keep answers practical and short.
"""


class AgentConjugation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    praesens: list[str] = Field(min_length=6, max_length=6)
    perfekt: list[str] = Field(min_length=6, max_length=6)
    praeteritum: list[str] = Field(min_length=6, max_length=6)


class AgentWordCard(BaseModel):
    """Strict fixed-shape schema exposed to the model tool."""

    model_config = ConfigDict(extra="forbid")

    lemma: str = Field(min_length=1)
    kind: Literal["noun", "verb", "verb_prep", "other"]
    translation: str = Field(min_length=1)
    example: str = Field(min_length=1)
    pronunciation: str = Field(min_length=1)
    article: str | None
    singular: str | None
    plural_full: str | None
    conjugation: AgentConjugation | None
    verb: str | None
    preposition: str | None
    case: str | None

    @model_validator(mode="after")
    def kind_fields(self) -> "AgentWordCard":
        if self.kind == "noun" and not all((self.article, self.singular, self.plural_full)):
            raise ValueError("noun fields are required")
        if self.kind == "verb" and self.conjugation is None:
            raise ValueError("conjugation is required")
        if self.kind == "verb_prep" and not all((self.verb, self.preposition, self.case)):
            raise ValueError("verb-preposition fields are required")
        return self

    def database_card(self) -> WordCard:
        value = self.model_dump(exclude_none=True)
        if self.conjugation is not None:
            value["conjugation"] = self.conjugation.model_dump()
        return WordCard.model_validate(value)


@dataclass
class TutorContext:
    database: db.Database
    learner: dict[str, Any]
    proposed: list[dict[str, Any]] = field(default_factory=list)


@function_tool
async def lookup_word(ctx: RunContextWrapper[TutorContext], query: str) -> str:
    """Look up one word in this learner's vocabulary."""
    word = await words.get_word(ctx.context.database, ctx.context.learner["id"], query)
    if not word:
        return json.dumps({"found": False})
    return json.dumps(
        {"found": True, "word_id": word.db_id, "card": db.word_to_card(word)},
        ensure_ascii=False,
        default=str,
    )


@function_tool
async def learner_stats(ctx: RunContextWrapper[TutorContext]) -> str:
    """Read the acting learner's aggregate training statistics."""
    value = await scheduler.stats(ctx.context.database, ctx.context.learner["id"])
    return json.dumps(value, ensure_ascii=False, default=str)


@function_tool
async def recent_errors(ctx: RunContextWrapper[TutorContext], limit: int = 10) -> str:
    """Read this learner's recent review results."""
    rows = await scheduler.history(
        ctx.context.database, ctx.context.learner["id"], limit=min(max(limit, 1), 30)
    )
    return json.dumps(rows, ensure_ascii=False, default=str)


@function_tool
async def due_words(ctx: RunContextWrapper[TutorContext], limit: int = 10) -> str:
    """Read due words for this learner only."""
    rows = await scheduler.list_due(
        ctx.context.database, ctx.context.learner["id"], min(max(limit, 1), 30)
    )
    safe = [
        {key: row[key] for key in ("id", "lemma", "kind", "language", "due_at", "reps")}
        for row in rows
    ]
    return json.dumps(safe, ensure_ascii=False, default=str)


@function_tool
async def learner_decks(ctx: RunContextWrapper[TutorContext]) -> str:
    """List this learner's decks and word counts."""
    rows = await words.list_decks(ctx.context.database, ctx.context.learner["id"])
    return json.dumps(rows, ensure_ascii=False, default=str)


@function_tool
async def curator_status(ctx: RunContextWrapper[TutorContext]) -> str:
    """Read the learner's current eligible push plan and last curator run."""
    plan = await curator.fresh_plan(ctx.context.database, ctx.context.learner["id"])
    async with ctx.context.database.connection() as conn:
        run = await db.fetch_one(
            conn,
            """SELECT kind, status, error, started_at, finished_at FROM curator_runs
               WHERE user_id = %s ORDER BY started_at DESC LIMIT 1""",
            (ctx.context.learner["id"],),
        )
    return json.dumps({"plan": plan, "last_run": run}, ensure_ascii=False, default=str)


@function_tool
async def current_task(ctx: RunContextWrapper[TutorContext], task_id: str) -> str:
    """Read task context; expected answers remain hidden until deterministic grading."""
    value = await scheduler.task_context(
        ctx.context.database, ctx.context.learner["id"], task_id
    )
    return json.dumps(value, ensure_ascii=False, default=str)


@function_tool
async def propose_words(
    ctx: RunContextWrapper[TutorContext],
    language: str,
    deck_name: str,
    cards: list[AgentWordCard],
) -> str:
    """Stage complete word cards for learner confirmation; this never changes progress."""
    staged = await words.stage_cards(
        ctx.context.database,
        user_id=ctx.context.learner["id"],
        language=language,
        deck_name=deck_name,
        cards=[card.database_card().model_dump(exclude_none=True) for card in cards],
    )
    ctx.context.proposed.append(staged)
    return json.dumps(staged, ensure_ascii=False)


TOOLS = [
    lookup_word,
    learner_stats,
    recent_errors,
    due_words,
    learner_decks,
    curator_status,
    current_task,
    propose_words,
]


@dataclass(frozen=True)
class TutorReply:
    text: str
    proposals: list[dict[str, Any]]


class TutorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if settings.openai_api_key:
            os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())

    @property
    def available(self) -> bool:
        return self.settings.openai_api_key is not None

    async def reply(
        self, database: db.Database, learner: dict[str, Any], text: str
    ) -> TutorReply:
        if not self.available:
            raise RuntimeError("OpenAI is not configured")
        usage_id = await llm.reserve(
            database,
            learner["id"],
            "tutor",
            Decimal(str(self.settings.llm_reservation_usd)),
        )
        context = TutorContext(database=database, learner=learner)
        api_started = False
        try:
            history = await llm.chat_history(database, learner["id"], limit=20)
            agent: Agent[TutorContext] = Agent(
                name="German tutor",
                instructions=INSTRUCTIONS,
                model=self.settings.tutor_model,
                tools=TOOLS,
            )
            api_started = True
            result = await Runner.run(
                agent,
                [*history, {"role": "user", "content": text}],
                context=context,
                max_turns=6,
                run_config=RunConfig(tracing_disabled=True, trace_include_sensitive_data=False),
            )
            usage = result.context_wrapper.usage
            input_price, output_price = self.settings.prices_for("tutor")
            actual = (
                Decimal(usage.input_tokens) * Decimal(str(input_price))
                + Decimal(usage.output_tokens) * Decimal(str(output_price))
            ) / Decimal(1_000_000)
            await llm.reconcile(
                database,
                usage_id,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                actual_usd=actual,
            )
        except Exception:
            if not api_started:
                await llm.release(database, usage_id)
            raise
        reply = str(result.final_output)
        await llm.append_chat(database, learner["id"], "user", text)
        await llm.append_chat(database, learner["id"], "assistant", reply)
        return TutorReply(reply, list(context.proposed))
