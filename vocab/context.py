"""Learner-scoped selection and persistence for generated context exercises."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from . import db, srs, scheduler
from .exercises.base import display_card_lemma

CONTEXT_TASK_TYPE = "context_llm"
CONTEXT_GENERATOR_VERSION = "context-v2"


class DuplicateContextError(RuntimeError):
    pass


def _json_object(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _fingerprint(situation: str, task: str) -> str:
    value = re.sub(r"\s+", " ", f"{situation} {task}".strip()).casefold()
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _visible_task(row: dict[str, Any]) -> dict[str, Any]:
    payload = _json_object(row["payload"])
    card = _json_object(row["card"])
    return {
        "task_id": row["id"],
        "type": CONTEXT_TASK_TYPE,
        "word": display_card_lemma(card, row["lemma"]),
        "word_id": row["word_id"],
        "language": row["language"],
        "stage": int(payload.get("stage") or 0),
        "response_mode": "choice" if payload.get("options") else "free_text",
        "answer_language": row["language"],
        "grading_policy": "deterministic"
        if payload.get("objective") == "contrast"
        else "tutor_on_mismatch",
        "objective": payload.get("objective", "free_expression"),
        **payload,
    }


async def candidate(
    database: db.Database, user_id: int, session_id: str
) -> dict[str, Any] | None:
    """Pick the least-used context card and include its recent generated contexts."""
    async with database.connection() as conn:
        session = await db.fetch_one(
            conn,
            """SELECT * FROM sessions
               WHERE id = %s AND user_id = %s AND kind = 'context' AND status = 'open'""",
            (session_id, user_id),
        )
        if not session:
            return None
        comparison = _json_object(session.get("comparison"))
        row = await db.fetch_one(
            conn,
            """SELECT w.*, p.reps, p.lapses, p.interval_days, p.due_at,
                      COALESCE(seen.seen_count, 0) AS context_seen_count
               FROM words w
               JOIN decks d ON d.id = w.deck_id
               LEFT JOIN progress p ON p.word_id = w.id
               LEFT JOIN (
                   SELECT word_id, count(*)::int AS seen_count
                   FROM tasks
                   WHERE session_id = %s AND type = 'context_llm'
                   GROUP BY word_id
               ) seen ON seen.word_id = w.id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive
                 AND (%s::bigint IS NULL OR w.deck_id = %s)
                 AND (%s::bigint IS NULL OR w.id = %s)
               ORDER BY COALESCE(seen.seen_count, 0),
                        CASE
                            WHEN w.kind = 'phrase' THEN 0
                            WHEN COALESCE(p.lapses, 0) > 0 THEN 1
                            WHEN w.kind = 'verb_prep' THEN 2
                            ELSE 3
                        END,
                        COALESCE(p.lapses, 0) DESC,
                        md5(%s || ':' || w.id::text)
               LIMIT 1""",
            (
                session_id,
                user_id,
                session["deck_id"],
                session["deck_id"],
                comparison.get("word_id"),
                comparison.get("word_id"),
                session_id,
            ),
        )
        if not row:
            return None
        recent_rows = await db.fetch_all(
            conn,
            """SELECT payload FROM tasks
               WHERE user_id = %s AND word_id = %s AND type = 'context_llm'
               ORDER BY created_at DESC LIMIT 8""",
            (user_id, row["id"]),
        )
    recent = []
    for recent_row in reversed(recent_rows):
        payload = _json_object(recent_row["payload"])
        recent.append(
            {
                "context_tag": payload.get("context_tag", ""),
                "situation_ru": payload.get("situation_ru", ""),
                "task": payload.get("task", ""),
                "comparison_answer": payload.get("comparison_answer"),
            }
        )
    card = _json_object(row["card"])
    stage = srs.stage(
        int(row.get("reps") or 0), float(row.get("interval_days") or 0)
    )
    return {
        "session_id": session_id,
        "word_id": row["id"],
        "language": row["language"],
        "stage": stage,
        "card": card,
        "recent_contexts": recent,
        "comparison": comparison or None,
        "practice_step": int(row["context_seen_count"]),
    }


async def save_task(
    database: db.Database,
    user_id: int,
    candidate_data: dict[str, Any],
    generated: dict[str, Any],
    *,
    previous_task_id: str | None = None,
) -> dict[str, Any] | None:
    """Persist one validated generated exercise if its session and card remain active."""
    situation = str(generated["situation_ru"]).strip()
    task_text = str(generated["task"]).strip()
    fingerprint = _fingerprint(situation, task_text)
    payload = {
        "prompt": f"Ситуация: {situation}\n\n{task_text}",
        "situation_ru": situation,
        "task": task_text,
        "mode": generated["mode"],
        "context_tag": str(generated["context_tag"]).strip(),
        "context_fingerprint": fingerprint,
        "generator_version": CONTEXT_GENERATOR_VERSION,
        "stage": int(candidate_data["stage"]),
        "objective": "contrast"
        if candidate_data.get("comparison")
        else "free_expression",
    }
    expected = {
        "answer": str(generated["reference_answer"]).strip(),
        "reference_answer": str(generated["reference_answer"]).strip(),
        "accepted": [str(generated["reference_answer"]).strip()],
        "required_elements": [],
        "card_snapshot": dict(candidate_data["card"]),
        "explanation_ru": str(generated["explanation_ru"]).strip(),
        "context_tag": payload["context_tag"],
    }
    comparison = candidate_data.get("comparison")
    if comparison:
        if generated.get("comparison_verified") is not True:
            raise ValueError("comparison must be validated before persistence")
        left = display_card_lemma(
            candidate_data["card"], candidate_data["card"]["lemma"]
        )
        right = comparison["alternative"]
        options = [f"Только {left}", f"Только {right}", "Оба подходят"]
        index = {"first": 0, "second": 1, "both": 2}.get(
            generated.get("comparison_answer")
        )
        if index is None or left.casefold() == right.casefold():
            raise ValueError("invalid comparison options")
        lesson = str(generated.get("comparison_intro_ru") or "").strip()
        if candidate_data.get("practice_step", 0) == 0 and not lesson:
            raise ValueError("first comparison requires a short explanation")
        heading = f"Сравним: {left} / {right}"
        if candidate_data.get("practice_step", 0) == 0:
            heading += f"\n\n{lesson}"
        payload["prompt"] = (
            f"{heading}\n\n{payload['prompt']}\n\nПодходит только первое выражение, только второе или оба?"
        )
        payload["options"] = options
        payload["comparison_answer"] = generated["comparison_answer"]
        expected.update(
            answer=options[index], accepted=[options[index]], options=options
        )
        expected["comparison"] = {"first": left, "second": right}
    async with database.connection() as conn:
        async with conn.transaction():
            user_result = await conn.execute(
                "SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,)
            )
            if not await user_result.fetchone():
                raise LookupError("user not found")
            session = await db.fetch_one(
                conn,
                """SELECT * FROM sessions
                   WHERE id = %s AND user_id = %s AND kind = 'context'
                     AND status = 'open' FOR UPDATE""",
                (candidate_data["session_id"], user_id),
            )
            if not session:
                return None
            if previous_task_id and not await scheduler._can_advance(
                conn, user_id, session["id"], previous_task_id
            ):
                return {"already_active": True}
            existing = await db.fetch_one(
                conn,
                """SELECT t.*, w.lemma, w.language, w.card
                   FROM tasks t JOIN words w ON w.id = t.word_id
                   WHERE t.user_id = %s AND t.status = 'open' AND t.expires_at > now()""",
                (user_id,),
            )
            if existing:
                return _visible_task(existing)
            word = await db.fetch_one(
                conn,
                """SELECT w.* FROM words w JOIN decks d ON d.id = w.deck_id
                   WHERE w.id = %s AND w.user_id = %s AND w.card_status = 'active'
                     AND NOT d.is_archive
                     AND (%s::bigint IS NULL OR w.deck_id = %s)""",
                (
                    candidate_data["word_id"],
                    user_id,
                    session["deck_id"],
                    session["deck_id"],
                ),
            )
            if not word:
                return None
            duplicate = await db.fetch_one(
                conn,
                """SELECT id FROM tasks
                   WHERE user_id = %s AND word_id = %s AND type = 'context_llm'
                     AND payload->>'context_fingerprint' = %s LIMIT 1""",
                (user_id, word["id"], fingerprint),
            )
            if duplicate:
                raise DuplicateContextError("generated context duplicates a previous task")
            task_id = uuid.uuid4().hex[:16]
            await conn.execute(
                """INSERT INTO tasks(
                       id, user_id, word_id, session_id, type, payload, expected,
                       response_mode, answer_language, grading_policy, objective, expires_at
                   ) VALUES (%s, %s, %s, %s, 'context_llm', %s, %s,
                             %s, %s, %s, %s,
                             now() + interval '24 hours')""",
                (
                    task_id,
                    user_id,
                    word["id"],
                    session["id"],
                    Jsonb(payload),
                    Jsonb(expected),
                    "choice" if comparison else "free_text",
                    word["language"],
                    "deterministic" if comparison else "tutor_on_mismatch",
                    payload["objective"],
                ),
            )
            await conn.execute(
                "UPDATE sessions SET last_activity_at = now() WHERE id = %s",
                (session["id"],),
            )
            return _visible_task(
                {
                    "id": task_id,
                    "word_id": word["id"],
                    "lemma": word["lemma"],
                    "language": word["language"],
                    "card": word["card"],
                    "payload": payload,
                }
            )
