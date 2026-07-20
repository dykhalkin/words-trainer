"""Transactional task, session, queue, and push scheduling."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import db, srs
from .exercises import GENERATORS
from .languages import ExerciseContext, language_spec, normalize_lemma

STAGE_TYPES = {
    0: ["choice"],
    1: ["flashcard_de_ru"],
    2: ["flashcard_ru_de", "cloze"],
    3: ["cloze", "grammar", "flashcard_ru_de"],
}
FALLBACK = ["flashcard_de_ru", "choice"]
DEFAULT_NEW_SCAN = 5
STARTED_SCAN_LIMIT = 100
TASK_TTL = timedelta(hours=24)
SESSION_IDLE_TTL = timedelta(minutes=30)


def _stage(row: dict[str, Any]) -> int:
    return srs.stage(int(row.get("reps") or 0), float(row.get("interval_days") or 0))


async def _candidate_rows(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    queue: str,
    deck_id: int | None = None,
    limit: int = STARTED_SCAN_LIMIT,
) -> list[dict[str, Any]]:
    deck_clause = " AND w.deck_id = %s" if deck_id is not None else ""
    params: list[Any] = [user_id]
    if deck_id is not None:
        params.append(deck_id)

    if queue in {"due", "review", "learning"}:
        due_clause = " AND p.due_at <= now()" if queue != "learning" else ""
        params.append(limit)
        rows = await db.fetch_all(
            conn,
            """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
               FROM progress p JOIN words w ON w.id = p.word_id
               WHERE w.user_id = %s"""
            + deck_clause
            + due_clause
            + " ORDER BY p.due_at NULLS FIRST, w.id LIMIT %s",
            tuple(params),
        )
        if queue == "review":
            return [row for row in rows if _stage(row) == 3]
        if queue == "learning":
            return [row for row in rows if _stage(row) < 3]
        return rows

    if queue == "new":
        params.append(limit)
        return await db.fetch_all(
            conn,
            """SELECT w.* FROM words w
               LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s"""
            + deck_clause
            + " AND p.word_id IS NULL ORDER BY w.id LIMIT %s",
            tuple(params),
        )
    raise ValueError(f"unsupported queue: {queue}")


async def _pick_row(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    queue: str,
    deck_id: int | None,
    rng: random.Random,
) -> dict[str, Any] | None:
    if queue == "auto":
        due = await _candidate_rows(
            conn, user_id=user_id, queue="due", deck_id=deck_id, limit=10
        )
        if due:
            return due[0]
        new = await _candidate_rows(
            conn,
            user_id=user_id,
            queue="new",
            deck_id=deck_id,
            limit=DEFAULT_NEW_SCAN,
        )
        return rng.choice(new) if new else None
    rows = await _candidate_rows(
        conn, user_id=user_id, queue=queue, deck_id=deck_id, limit=STARTED_SCAN_LIMIT
    )
    if not rows:
        return None
    return rng.choice(rows[:DEFAULT_NEW_SCAN]) if queue == "new" else rows[0]


async def _row_by_query(
    conn: AsyncConnection[dict[str, Any]],
    *,
    user_id: int,
    word_id: int | None,
    word_query: str | None,
    language: str | None,
) -> dict[str, Any] | None:
    if word_id is not None:
        return await db.fetch_one(
            conn,
            """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
               FROM words w LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s AND w.id = %s""",
            (user_id, word_id),
        )
    if word_query is None:
        return None
    params: list[Any] = [user_id, f"%{word_query.strip()}%"]
    language_clause = ""
    if language:
        language_clause = " AND w.language = %s"
        params.append(language)
    return await db.fetch_one(
        conn,
        """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at
           FROM words w LEFT JOIN progress p ON p.word_id = w.id
           WHERE w.user_id = %s AND w.lemma ILIKE %s"""
        + language_clause
        + " ORDER BY length(w.lemma), w.id LIMIT 1",
        tuple(params),
    )


async def create_task(
    database: db.Database,
    user_id: int,
    rng: random.Random | None = None,
    *,
    word_id: int | None = None,
    word_query: str | None = None,
    language: str | None = None,
    task_type: str | None = None,
    queue: str = "auto",
    deck_id: int | None = None,
    session_id: str | None = None,
) -> dict[str, Any] | None:
    rng = rng or random.Random()
    async with database.connection() as conn:
        async with conn.transaction():
            user_lock = await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
            if not await user_lock.fetchone():
                raise LookupError("user not found")
            if session_id is not None:
                session_result = await conn.execute(
                    """SELECT * FROM sessions
                       WHERE id = %s AND user_id = %s AND status = 'open' FOR UPDATE""",
                    (session_id, user_id),
                )
                session = await session_result.fetchone()
                if not session:
                    return None
                if session["deck_id"] is not None:
                    deck_id = session["deck_id"]
                if (
                    session["kind"] == "micro"
                    and word_id is None
                    and word_query is None
                ):
                    queue = "due"
            if word_id is not None or word_query is not None:
                row = await _row_by_query(
                    conn,
                    user_id=user_id,
                    word_id=word_id,
                    word_query=word_query,
                    language=language,
                )
            else:
                row = await _pick_row(
                    conn, user_id=user_id, queue=queue, deck_id=deck_id, rng=rng
                )
            if not row:
                return None
            word = db.word_from_row(row)
            stage = _stage(row)
            context = ExerciseContext(user_id=user_id, language=word.language)
            allowed = set(language_spec(word.language).exercise_types)
            if task_type:
                if task_type not in allowed:
                    return None
                types = [task_type]
            else:
                types = [name for name in STAGE_TYPES[stage] if name in allowed]
                types.extend(name for name in FALLBACK if name in allowed and name not in types)
            produced: tuple[dict[str, Any], dict[str, Any]] | None = None
            selected_type = ""
            for name in types:
                generator = GENERATORS.get(name)
                if not generator:
                    continue
                produced = await generator.generate(word, conn, rng, context)
                if produced is not None:
                    selected_type = name
                    break
                if task_type:
                    return None
            if produced is None:
                return None
            payload, expected = produced
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            task_id = uuid.uuid4().hex[:16]
            await conn.execute(
                """INSERT INTO tasks(
                       id, user_id, word_id, session_id, type, payload, expected, expires_at
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    task_id,
                    user_id,
                    row["id"],
                    session_id,
                    selected_type,
                    Jsonb(payload),
                    Jsonb(expected),
                    datetime.now(timezone.utc) + TASK_TTL,
                ),
            )
            if session_id:
                await conn.execute(
                    """UPDATE sessions SET last_activity_at = now()
                       WHERE id = %s AND user_id = %s AND status = 'open'""",
                    (session_id, user_id),
                )
            return {
                "task_id": task_id,
                "type": selected_type,
                "word": word.lemma,
                "word_id": row["id"],
                "language": word.language,
                "stage": stage,
                **payload,
            }


def _json_object(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def submit_answer(
    database: db.Database, user_id: int, task_id: str, answer: str
) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """SELECT t.*, w.lemma, w.language, w.card
                   FROM tasks t JOIN words w ON w.id = t.word_id
                   WHERE t.id = %s AND t.user_id = %s FOR UPDATE OF t""",
                (task_id, user_id),
            )
            task = await result.fetchone()
            if not task:
                return {"error": "exercise expired", "task_id": task_id}
            if task["status"] != "open" or task["expires_at"] <= datetime.now(timezone.utc):
                if task["status"] == "open":
                    await conn.execute(
                        "UPDATE tasks SET status = 'voided', voided_at = now() WHERE id = %s",
                        (task_id,),
                    )
                return {"error": "exercise expired", "task_id": task_id}

            expected = _json_object(task["expected"])
            checked = GENERATORS[task["type"]].check(expected, answer)
            progress_result = await conn.execute(
                "SELECT * FROM progress WHERE word_id = %s FOR UPDATE", (task["word_id"],)
            )
            progress = await progress_result.fetchone()
            schedule = srs.review(
                reps=progress["reps"] if progress else 0,
                lapses=progress["lapses"] if progress else 0,
                ease=progress["ease"] if progress else 2.5,
                interval_days=progress["interval_days"] if progress else 0.0,
                quality=checked.quality,
            )
            await conn.execute(
                """INSERT INTO progress(word_id, reps, lapses, ease, interval_days, due_at)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT(word_id) DO UPDATE SET
                       reps = EXCLUDED.reps,
                       lapses = EXCLUDED.lapses,
                       ease = EXCLUDED.ease,
                       interval_days = EXCLUDED.interval_days,
                       due_at = EXCLUDED.due_at,
                       updated_at = now()""",
                (
                    task["word_id"],
                    schedule.reps,
                    schedule.lapses,
                    schedule.ease,
                    schedule.interval_days,
                    schedule.due_at,
                ),
            )
            verdict = {
                "correct": checked.correct,
                "expected": checked.expected,
                "note": checked.note,
                "next_review_at": schedule.due_at,
                "interval_days": schedule.interval_days,
                "stage": srs.stage(schedule.reps, schedule.interval_days),
            }
            await conn.execute(
                """UPDATE tasks SET status = 'answered', answered_at = now(), answer = %s,
                       correct = %s, verdict = %s WHERE id = %s""",
                (answer, checked.correct, Jsonb(verdict), task_id),
            )
            await conn.execute(
                """INSERT INTO reviews(user_id, word_id, task_id, task_type, answer, correct, quality)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    user_id,
                    task["word_id"],
                    task_id,
                    task["type"],
                    answer,
                    checked.correct,
                    checked.quality,
                ),
            )
            session_complete = False
            session_answered_count: int | None = None
            session_correct_count: int | None = None
            if task["session_id"]:
                updated = await conn.execute(
                    """UPDATE sessions SET
                           answered_count = answered_count + 1,
                           correct_count = correct_count + %s,
                           last_activity_at = now()
                       WHERE id = %s AND user_id = %s AND status = 'open'
                       RETURNING *""",
                    (int(checked.correct), task["session_id"], user_id),
                )
                session = await updated.fetchone()
                if session:
                    session_answered_count = session["answered_count"]
                    session_correct_count = session["correct_count"]
                if session and session["target_count"] is not None:
                    session_complete = session["answered_count"] >= session["target_count"]
                    if session_complete:
                        await conn.execute(
                            """UPDATE sessions SET status = 'completed', ended_at = now()
                               WHERE id = %s""",
                            (task["session_id"],),
                        )
            card = _json_object(task["card"])
            return {
                "task_id": task_id,
                "correct": checked.correct,
                "expected": checked.expected,
                "note": checked.note,
                "word": task["lemma"],
                "translation": card.get("translation", ""),
                "next_review_at": schedule.due_at,
                "interval_days": schedule.interval_days,
                "stage": verdict["stage"],
                "session_complete": session_complete,
                "session_answered_count": session_answered_count,
                "session_correct_count": session_correct_count,
            }


async def get_open_task(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT id AS task_id, type, payload, word_id, session_id, created_at, expires_at
               FROM tasks WHERE user_id = %s AND status = 'open' AND expires_at > now()""",
            (user_id,),
        )


async def task_context(
    database: db.Database, user_id: int, task_id: str
) -> dict[str, Any] | None:
    async with database.connection() as conn:
        row = await db.fetch_one(
            conn,
            """SELECT t.*, w.lemma, w.card FROM tasks t JOIN words w ON w.id = t.word_id
               WHERE t.id = %s AND t.user_id = %s""",
            (task_id, user_id),
        )
    if not row:
        return None
    output = {
        "task_id": row["id"],
        "status": row["status"],
        "type": row["type"],
        "payload": _json_object(row["payload"]),
        "word": row["lemma"],
        "card": _json_object(row["card"]),
        "answer": row["answer"],
        "verdict": _json_object(row["verdict"]) if row["verdict"] else None,
    }
    if row["status"] == "answered":
        output["expected"] = _json_object(row["expected"])
    return output


async def sweep_expired(database: db.Database) -> int:
    async with database.connection() as conn:
        result = await conn.execute(
            """UPDATE tasks SET status = 'voided', voided_at = now()
               WHERE status = 'open' AND expires_at <= now()"""
        )
        return result.rowcount


async def start_session(
    database: db.Database,
    user_id: int,
    *,
    kind: str,
    deck_id: int | None = None,
    target_count: int | None = None,
) -> dict[str, Any]:
    if kind not in {"micro", "long"}:
        raise ValueError("session kind must be micro or long")
    if kind == "micro" and target_count is None:
        target_count = 5
    session_id = uuid.uuid4().hex[:16]
    async with database.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (user_id,))
            await conn.execute(
                """UPDATE sessions SET status = 'stopped', ended_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            result = await conn.execute(
                """INSERT INTO sessions(id, user_id, kind, deck_id, target_count)
                   VALUES (%s, %s, %s, %s, %s) RETURNING *""",
                (session_id, user_id, kind, deck_id, target_count),
            )
            return await result.fetchone()


async def get_open_session(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn, "SELECT * FROM sessions WHERE user_id = %s AND status = 'open'", (user_id,)
        )


async def stop_session(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE sessions SET status = 'stopped', ended_at = now()
                   WHERE user_id = %s AND status = 'open' RETURNING *""",
                (user_id,),
            )
            session = await result.fetchone()
            await conn.execute(
                """UPDATE tasks SET status = 'voided', voided_at = now()
                   WHERE user_id = %s AND status = 'open'""",
                (user_id,),
            )
            return session


async def close_idle_sessions(database: db.Database) -> int:
    cutoff = datetime.now(timezone.utc) - SESSION_IDLE_TTL
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """UPDATE sessions SET status = 'expired', ended_at = now()
                   WHERE status = 'open' AND last_activity_at < %s RETURNING user_id""",
                (cutoff,),
            )
            rows = await result.fetchall()
            if rows:
                await conn.execute(
                    """UPDATE tasks SET status = 'voided', voided_at = now()
                       WHERE status = 'open' AND user_id = ANY(%s)""",
                    ([row["user_id"] for row in rows],),
                )
            return len(rows)


def _in_quiet_hours(user: dict[str, Any], now: datetime) -> bool:
    local = now.astimezone(ZoneInfo(user["timezone"])).time().replace(tzinfo=None)
    start = user["quiet_start"]
    end = user["quiet_end"]
    if start < end:
        return start <= local < end
    return local >= start or local < end


async def _due_rows_for_push(
    conn: AsyncConnection[dict[str, Any]], user_id: int, limit: int = 5
) -> tuple[list[dict[str, Any]], bool]:
    rows = await db.fetch_all(
        conn,
        """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at,
                  lr.correct AS last_review_correct
           FROM progress p JOIN words w ON w.id = p.word_id
           LEFT JOIN LATERAL (
               SELECT correct FROM reviews r WHERE r.word_id = w.id
               ORDER BY r.created_at DESC LIMIT 1
           ) lr ON true
           WHERE w.user_id = %s AND p.due_at <= now()
           ORDER BY p.due_at, w.id LIMIT %s""",
        (user_id, limit),
    )
    has_non_lapse_trigger = any(
        not (
            row["reps"] == 0
            and float(row["interval_days"]) == 0
            and row["last_review_correct"] is False
        )
        for row in rows
    )
    return rows, has_non_lapse_trigger


def _has_push_trigger(rows: list[dict[str, Any]]) -> bool:
    return any(
        not (
            row["reps"] == 0
            and float(row["interval_days"]) == 0
            and row["last_review_correct"] is False
        )
        for row in rows
    )


async def _push_rows(
    conn: AsyncConnection[dict[str, Any]], user_id: int, limit: int
) -> tuple[list[dict[str, Any]], bool, str]:
    plan = await db.fetch_one(
        conn,
        """SELECT p.plan FROM curator_plans p
           WHERE p.user_id = %s AND p.kind = 'plan'
             AND p.created_at >= now() - interval '24 hours'
             AND NOT EXISTS (
                 SELECT 1 FROM curator_runs r
                 WHERE r.user_id = p.user_id AND r.kind = p.kind
                   AND r.status = 'failed' AND r.started_at > p.created_at
             )
           ORDER BY p.created_at DESC LIMIT 1""",
        (user_id,),
    )
    if plan:
        value = _json_object(plan["plan"])
        word_ids = [int(item["word_id"]) for item in value.get("focus", []) if item.get("word_id")]
        if word_ids:
            rows = await db.fetch_all(
                conn,
                """SELECT w.*, p.reps, p.lapses, p.ease, p.interval_days, p.due_at,
                          lr.correct AS last_review_correct
                   FROM progress p JOIN words w ON w.id = p.word_id
                   LEFT JOIN LATERAL (
                       SELECT correct FROM reviews r WHERE r.word_id = w.id
                       ORDER BY r.created_at DESC LIMIT 1
                   ) lr ON true
                   WHERE w.user_id = %s AND p.due_at <= now() AND w.id = ANY(%s)
                   ORDER BY array_position(%s::bigint[], w.id) LIMIT %s""",
                (user_id, word_ids, word_ids, limit),
            )
            if rows:
                return rows, _has_push_trigger(rows), "curator"
    rows, trigger = await _due_rows_for_push(conn, user_id, limit)
    return rows, trigger, "deterministic"


async def compose_push(database: db.Database, user_id: int, limit: int = 5) -> dict[str, Any]:
    async with database.connection() as conn:
        rows, _, source = await _push_rows(conn, user_id, limit)
    return {"user_id": user_id, "word_ids": [row["id"] for row in rows], "source": source}


async def claim_push(
    database: db.Database, user_id: int, *, now: datetime | None = None, limit: int = 5
) -> dict[str, Any] | None:
    now = now or datetime.now(timezone.utc)
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute("SELECT * FROM users WHERE id = %s FOR UPDATE", (user_id,))
            user = await result.fetchone()
            if not user or _in_quiet_hours(user, now):
                return None
            long_session = await db.fetch_one(
                conn,
                """SELECT id FROM sessions
                   WHERE user_id = %s AND kind = 'long' AND status = 'open'""",
                (user_id,),
            )
            if long_session:
                return None
            last = await db.fetch_one(
                conn,
                """SELECT claimed_at FROM deliveries
                   WHERE user_id = %s AND kind = 'push' AND status IN ('claimed', 'sent')
                   ORDER BY claimed_at DESC LIMIT 1""",
                (user_id,),
            )
            if last and now - last["claimed_at"] < timedelta(
                minutes=user["min_push_interval_minutes"]
            ):
                return None
            rows, has_trigger, source = await _push_rows(conn, user_id, limit)
            if not rows or not has_trigger:
                return None
            interval_seconds = user["min_push_interval_minutes"] * 60
            bucket = int(now.timestamp()) // interval_seconds
            key = f"push:{user_id}:{bucket}"
            payload = {"word_ids": [row["id"] for row in rows], "source": source}
            inserted = await conn.execute(
                """INSERT INTO deliveries(user_id, kind, idempotency_key, payload, claimed_at)
                   VALUES (%s, 'push', %s, %s, %s)
                   ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
                (user_id, key, Jsonb(payload), now),
            )
            return await inserted.fetchone()


async def mark_delivery_sent(
    database: db.Database, delivery_id: int, telegram_message_id: int
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE deliveries SET status = 'sent', telegram_message_id = %s, sent_at = now()
               WHERE id = %s AND status = 'claimed'""",
            (telegram_message_id, delivery_id),
        )


async def mark_delivery_failed(
    database: db.Database, delivery_id: int, error: str, *, release: bool = False
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """UPDATE deliveries SET status = %s, error = %s
               WHERE id = %s AND status = 'claimed'""",
            ("released" if release else "failed", error[:1000], delivery_id),
        )


async def claim_digest(
    database: db.Database, user_id: int, run_date: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """Claim one weekly digest per learner/date across overlaps and restarts."""
    key = f"digest:{user_id}:{run_date}"
    async with database.connection() as conn:
        result = await conn.execute(
            """INSERT INTO deliveries(user_id, kind, idempotency_key, payload)
               VALUES (%s, 'digest', %s, %s)
               ON CONFLICT(idempotency_key) DO NOTHING RETURNING *""",
            (user_id, key, Jsonb(payload)),
        )
        return await result.fetchone()


async def list_due(database: db.Database, user_id: int, limit: int = 20) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        return await _candidate_rows(conn, user_id=user_id, queue="due", limit=limit)


async def history(
    database: db.Database, user_id: int, *, word_id: int | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    word_clause = " AND r.word_id = %s" if word_id is not None else ""
    params: list[Any] = [user_id]
    if word_id is not None:
        params.append(word_id)
    params.append(limit)
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT r.*, w.lemma FROM reviews r JOIN words w ON w.id = r.word_id
               WHERE r.user_id = %s"""
            + word_clause
            + " ORDER BY r.created_at DESC LIMIT %s",
            tuple(params),
        )


async def stats(database: db.Database, user_id: int) -> dict[str, Any]:
    async with database.connection() as conn:
        counts = await db.fetch_one(
            conn,
            """SELECT count(*)::int AS total,
                      count(p.word_id)::int AS studied,
                      count(*) FILTER (WHERE p.due_at <= now())::int AS due
               FROM words w LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s""",
            (user_id,),
        )
        progress_rows = await db.fetch_all(
            conn,
            """SELECT p.reps, p.interval_days FROM progress p
               JOIN words w ON w.id = p.word_id WHERE w.user_id = %s""",
            (user_id,),
        )
        week = await db.fetch_one(
            conn,
            """SELECT count(*)::int AS count,
                      count(*) FILTER (WHERE correct)::int AS correct
               FROM reviews WHERE user_id = %s AND created_at >= now() - interval '7 days'""",
            (user_id,),
        )
    by_stage = {str(index): 0 for index in range(4)}
    for row in progress_rows:
        by_stage[str(_stage(row))] += 1
    return {
        "words_total": counts["total"],
        "words_new": counts["total"] - counts["studied"],
        "words_studied": counts["studied"],
        "due_now": counts["due"],
        "by_stage": by_stage,
        "reviews_last_7d": {
            "count": week["count"],
            "correct": week["correct"],
            "accuracy": round(week["correct"] / week["count"], 2) if week["count"] else None,
        },
    }
