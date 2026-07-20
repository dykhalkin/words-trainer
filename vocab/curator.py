"""Deterministic learner analysis and curator plan persistence."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from . import db


async def analyze(database: db.Database, user_id: int) -> dict[str, Any]:
    """Compute model input without granting the curator arbitrary query tools."""
    async with database.connection() as conn:
        learner = await db.fetch_one(
            conn, "SELECT id, timezone FROM users WHERE id = %s", (user_id,)
        )
        if not learner:
            raise LookupError("user not found")
        hard_words = await db.fetch_all(
            conn,
            """SELECT w.id AS word_id, w.lemma, w.language,
                      count(r.id)::int AS reviews,
                      count(r.id) FILTER (WHERE NOT r.correct)::int AS errors,
                      round(avg(r.quality), 2) AS average_quality
               FROM words w JOIN decks d ON d.id = w.deck_id
               JOIN reviews r ON r.word_id = w.id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive
               GROUP BY w.id
               ORDER BY count(r.id) FILTER (WHERE NOT r.correct) DESC,
                        avg(r.quality), w.id
               LIMIT 30""",
            (user_id,),
        )
        by_type = await db.fetch_all(
            conn,
            """SELECT task_type, count(*)::int AS reviews,
                      count(*) FILTER (WHERE correct)::int AS correct
               FROM reviews r JOIN words w ON w.id = r.word_id
               JOIN decks d ON d.id = w.deck_id
               WHERE r.user_id = %s AND r.created_at >= now() - interval '30 days'
                 AND w.card_status = 'active' AND NOT d.is_archive
               GROUP BY task_type ORDER BY task_type""",
            (user_id,),
        )
        overdue = await db.fetch_one(
            conn,
            """SELECT
                   count(*) FILTER (WHERE p.due_at > now() - interval '1 day')::int AS under_1d,
                   count(*) FILTER (WHERE p.due_at <= now() - interval '1 day'
                                      AND p.due_at > now() - interval '7 days')::int AS days_1_7,
                   count(*) FILTER (WHERE p.due_at <= now() - interval '7 days')::int AS over_7d
               FROM progress p JOIN words w ON w.id = p.word_id
               JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND p.due_at <= now()
                 AND w.card_status = 'active' AND NOT d.is_archive""",
            (user_id,),
        )
        recent = await db.fetch_all(
            conn,
            """SELECT r.word_id, r.correct FROM reviews r
               JOIN words w ON w.id = r.word_id JOIN decks d ON d.id = w.deck_id
               WHERE r.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive
               ORDER BY r.created_at DESC LIMIT 50""",
            (user_id,),
        )
    streaks: dict[int, int] = {}
    closed: set[int] = set()
    for row in recent:
        word_id = row["word_id"]
        if word_id in closed:
            continue
        if row["correct"]:
            closed.add(word_id)
        else:
            streaks[word_id] = streaks.get(word_id, 0) + 1
    return {
        "user_id": user_id,
        "generated_at": datetime.now(timezone.utc),
        "hard_words": hard_words,
        "accuracy_by_exercise": [
            {
                **row,
                "accuracy": round(row["correct"] / row["reviews"], 3)
                if row["reviews"]
                else None,
            }
            for row in by_type
        ],
        "overdue": overdue,
        "error_streaks": [
            {"word_id": word_id, "consecutive_errors": count}
            for word_id, count in sorted(streaks.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


async def local_run_date(database: db.Database, user_id: int):
    async with database.connection() as conn:
        user = await db.fetch_one(conn, "SELECT timezone FROM users WHERE id = %s", (user_id,))
    if not user:
        raise LookupError("user not found")
    return datetime.now(timezone.utc).astimezone(ZoneInfo(user["timezone"])).date()


async def save_plan(
    database: db.Database, user_id: int, kind: str, plan: dict[str, Any]
) -> dict[str, Any]:
    run_date = await local_run_date(database, user_id)
    async with database.connection() as conn:
        result = await conn.execute(
            """INSERT INTO curator_plans(user_id, run_date, kind, plan)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(user_id, run_date, kind) DO UPDATE SET
                   plan = EXCLUDED.plan, created_at = now()
               RETURNING *""",
            (user_id, run_date, kind, Jsonb(plan)),
        )
        return await result.fetchone()


async def fresh_plan(database: db.Database, user_id: int, kind: str = "plan") -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT p.* FROM curator_plans p
               WHERE p.user_id = %s AND p.kind = %s
                 AND p.created_at >= now() - interval '24 hours'
                 AND NOT EXISTS (
                     SELECT 1 FROM curator_runs r
                     WHERE r.user_id = p.user_id AND r.kind = p.kind
                       AND r.status = 'failed' AND r.started_at > p.created_at
                 )
               ORDER BY p.created_at DESC LIMIT 1""",
            (user_id, kind),
        )
