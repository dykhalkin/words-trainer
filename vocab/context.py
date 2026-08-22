"""Learner-scoped selection and persistence for generated context exercises."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from typing import Any

from psycopg.types.json import Jsonb

from . import db, srs
from .exercises.base import display_card_lemma

CONTEXT_TASK_TYPE = "context_llm"
CONTEXT_GENERATOR_VERSION = "context-v1"


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
        "response_mode": "free_text",
        "answer_language": row["language"],
        "grading_policy": "tutor_on_mismatch",
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
    }


async def save_task(
    database: db.Database,
    user_id: int,
    candidate_data: dict[str, Any],
    generated: dict[str, Any],
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
    }
    expected = {
        "answer": str(generated["reference_answer"]).strip(),
        "accepted": [str(generated["reference_answer"]).strip()],
        "required_elements": list(generated["required_elements"]),
        "explanation_ru": str(generated["explanation_ru"]).strip(),
        "context_tag": payload["context_tag"],
    }
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
                       response_mode, answer_language, grading_policy, expires_at
                   ) VALUES (%s, %s, %s, %s, 'context_llm', %s, %s,
                             'free_text', %s, 'tutor_on_mismatch',
                             now() + interval '24 hours')""",
                (
                    task_id,
                    user_id,
                    word["id"],
                    session["id"],
                    Jsonb(payload),
                    Jsonb(expected),
                    word["language"],
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
