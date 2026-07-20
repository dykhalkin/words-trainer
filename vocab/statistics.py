"""Learner and deck statistics with learner-local calendar buckets."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from . import db, srs


def _stage(row: dict[str, Any]) -> int:
    return srs.stage(int(row.get("reps") or 0), float(row.get("interval_days") or 0))


def _local_bounds(now: datetime, zone: ZoneInfo, days: int) -> tuple[list[date], datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if days < 1 or days > 90:
        raise ValueError("days must be between 1 and 90")
    today = now.astimezone(zone).date()
    dates = [today - timedelta(days=offset) for offset in reversed(range(days))]
    start = datetime.combine(dates[0], time.min, tzinfo=zone).astimezone(timezone.utc)
    end = datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone).astimezone(
        timezone.utc
    )
    return dates, start, end


async def stats(
    database: db.Database,
    user_id: int,
    *,
    deck_id: int | None = None,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    async with database.connection() as conn:
        user = await db.fetch_one(
            conn, "SELECT id, timezone FROM users WHERE id = %s", (user_id,)
        )
        if not user:
            raise LookupError("user not found")
        zone = ZoneInfo(user["timezone"])
        dates, activity_start, activity_end = _local_bounds(now, zone, days)
        today_start = datetime.combine(dates[-1], time.min, tzinfo=zone).astimezone(
            timezone.utc
        )

        deck: dict[str, Any] | None = None
        deck_clause = ""
        deck_params: list[Any] = []
        review_deck_clause = ""
        review_deck_params: list[Any] = []
        if deck_id is not None:
            deck = await db.fetch_one(
                conn,
                """SELECT * FROM decks
                   WHERE id = %s AND user_id = %s AND NOT is_archive""",
                (deck_id, user_id),
            )
            if not deck:
                raise LookupError("active deck not found")
            deck_clause = " AND w.deck_id = %s"
            deck_params.append(deck_id)
            review_deck_clause = " AND r.deck_id = %s"
            review_deck_params.append(deck_id)

        counts = await db.fetch_one(
            conn,
            """SELECT count(*)::int AS total,
                      count(p.word_id)::int AS studied,
                      count(*) FILTER (WHERE p.due_at <= %s)::int AS due
               FROM words w JOIN decks d ON d.id = w.deck_id
               LEFT JOIN progress p ON p.word_id = w.id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive"""
            + deck_clause,
            (now, user_id, *deck_params),
        )
        progress_rows = await db.fetch_all(
            conn,
            """SELECT p.reps, p.interval_days FROM progress p
               JOIN words w ON w.id = p.word_id JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive"""
            + deck_clause,
            (user_id, *deck_params),
        )
        needs_fix = await db.fetch_one(
            conn,
            """SELECT count(*)::int AS count FROM words w
               JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND w.card_status = 'needs_fix' AND NOT d.is_archive"""
            + deck_clause,
            (user_id, *deck_params),
        )
        archived = await db.fetch_one(
            conn,
            """SELECT count(*)::int AS count FROM words w
               JOIN decks d ON d.id = w.deck_id
               WHERE w.user_id = %s AND d.is_archive""",
            (user_id,),
        )
        review_rows = await db.fetch_all(
            conn,
            """SELECT r.word_id, r.correct, r.created_at
               FROM reviews r WHERE r.user_id = %s
                 AND r.created_at >= %s AND r.created_at < %s"""
            + review_deck_clause
            + " ORDER BY r.created_at",
            (user_id, activity_start, activity_end, *review_deck_params),
        )

    by_stage = {str(index): 0 for index in range(4)}
    for row in progress_rows:
        by_stage[str(_stage(row))] += 1

    daily = {
        day: {"date": day.isoformat(), "review_attempts": 0, "unique_words": set()}
        for day in dates
    }
    correct = 0
    for row in review_rows:
        local_day = row["created_at"].astimezone(zone).date()
        if local_day not in daily:
            continue
        daily[local_day]["review_attempts"] += 1
        daily[local_day]["unique_words"].add(row["word_id"])
        correct += int(row["correct"])
    daily_activity = [
        {
            "date": value["date"],
            "review_attempts": value["review_attempts"],
            "unique_words": len(value["unique_words"]),
        }
        for value in daily.values()
    ]
    today_rows = [row for row in review_rows if row["created_at"] >= today_start]
    total_reviews = len(review_rows)
    result = {
        "words_total": counts["total"],
        "words_new": counts["total"] - counts["studied"],
        "words_studied": counts["studied"],
        "due_now": counts["due"],
        "by_stage": by_stage,
        "needs_fix": needs_fix["count"],
        "archived": archived["count"] if deck_id is None else 0,
        "today": {
            "review_attempts": len(today_rows),
            "unique_words": len({row["word_id"] for row in today_rows}),
        },
        "reviews_last_7d": {
            "count": total_reviews,
            "correct": correct,
            "accuracy": round(correct / total_reviews, 2) if total_reviews else None,
        },
        "daily_activity": daily_activity,
        "days": days,
        "timezone": user["timezone"],
        "deck": (
            {key: deck[key] for key in ("id", "name", "language")}
            if deck is not None
            else None
        ),
    }
    return result


async def deck_stats(
    database: db.Database,
    user_id: int,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        decks = await db.fetch_all(
            conn,
            """SELECT id FROM decks
               WHERE user_id = %s AND NOT is_archive ORDER BY language, is_general DESC, name""",
            (user_id,),
        )
    return [
        await stats(database, user_id, deck_id=row["id"], days=days, now=now)
        for row in decks
    ]
