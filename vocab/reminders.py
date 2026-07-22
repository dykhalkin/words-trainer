"""Persistent learner reminder policies and validated delivery materialization."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import db

UTC = timezone.utc
PLANNING_HORIZON = timedelta(hours=48)
FREEZE_WINDOW = timedelta(minutes=15)
EXECUTION_GRACE = timedelta(minutes=15)
SYSTEM_MAX_PER_DAY = 6
ALLOWED_SUPPRESSION_REASONS = {
    "no_due",
    "recent_practice",
    "active_session",
    "low_due_load",
    "recently_ignored",
}


def _object(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


def _time_value(value: time | str) -> time:
    return value if isinstance(value, time) else time.fromisoformat(value)


def window_minutes(policy: dict[str, Any]) -> int:
    start = _time_value(policy["window_start"])
    end = _time_value(policy["window_end"])
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    return (end_minutes - start_minutes) % (24 * 60)


def daily_cap(policy: dict[str, Any]) -> int:
    return min(
        SYSTEM_MAX_PER_DAY,
        max(1, math.ceil(window_minutes(policy) / policy["target_interval_minutes"])),
    )


def weekday_enabled(days_mask: int, day: date) -> bool:
    return bool(days_mask & (1 << day.weekday()))


def _resolve_local(naive: datetime, zone: ZoneInfo) -> datetime:
    """Resolve fold=0 and advance nonexistent local minutes through a DST gap."""
    candidate = naive
    for _ in range(181):
        aware = candidate.replace(tzinfo=zone, fold=0)
        roundtrip = aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None)
        if roundtrip == candidate:
            return aware
        candidate += timedelta(minutes=1)
    raise ValueError("local time cannot be resolved")


def policy_window(policy: dict[str, Any], policy_day: date) -> tuple[datetime, datetime]:
    zone = ZoneInfo(policy["timezone"])
    start_time = _time_value(policy["window_start"])
    end_time = _time_value(policy["window_end"])
    start_naive = datetime.combine(policy_day, start_time)
    end_day = policy_day + timedelta(days=1) if start_time > end_time else policy_day
    end_naive = datetime.combine(end_day, end_time)
    return _resolve_local(start_naive, zone), _resolve_local(end_naive, zone)


def containing_policy_day(
    policy: dict[str, Any], local_value: datetime
) -> date | None:
    for candidate in (local_value.date(), local_value.date() - timedelta(days=1)):
        if not weekday_enabled(policy["days_mask"], candidate):
            continue
        start, end = policy_window(policy, candidate)
        if start <= local_value < end:
            return candidate
    return None


def _rounded_up(value: datetime, minutes: int = 5) -> datetime:
    discard = timedelta(
        minutes=value.minute % minutes,
        seconds=value.second,
        microseconds=value.microsecond,
    )
    return value if not discard else value - discard + timedelta(minutes=minutes)


async def get_policy(
    database: db.Database, user_id: int, *, for_update: bool = False
) -> dict[str, Any] | None:
    suffix = " FOR UPDATE" if for_update else ""
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT rp.*, u.timezone FROM reminder_policies rp
               JOIN users u ON u.id = rp.user_id WHERE rp.user_id = %s""" + suffix,
            (user_id,),
        )


async def _policy_row(
    conn: AsyncConnection[dict[str, Any]], user_id: int, *, lock: bool = False
) -> dict[str, Any] | None:
    suffix = " FOR UPDATE OF rp" if lock else ""
    return await db.fetch_one(
        conn,
        """SELECT rp.*, u.timezone FROM reminder_policies rp
           JOIN users u ON u.id = rp.user_id WHERE rp.user_id = %s""" + suffix,
        (user_id,),
    )


def validate_policy_values(
    *, days_mask: int, window_start: time, window_end: time, target_interval_minutes: int
) -> None:
    window_start = _time_value(window_start)
    window_end = _time_value(window_end)
    if not 1 <= days_mask <= 127:
        raise ValueError("at least one active weekday is required")
    if window_start == window_end:
        raise ValueError("reminder window start and end must differ")
    if not 60 <= target_interval_minutes <= 1440:
        raise ValueError("reminder cadence must be between 1 and 24 hours")


async def update_policy(
    database: db.Database,
    user_id: int,
    *,
    mode: str | None = None,
    days_mask: int | None = None,
    window_start: time | str | None = None,
    window_end: time | str | None = None,
    target_interval_minutes: int | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    if mode is not None and mode not in {"smart", "off"}:
        raise ValueError("reminder mode must be smart or off")
    async with database.connection() as conn:
        async with conn.transaction():
            current = await _policy_row(conn, user_id, lock=True)
            if not current:
                raise LookupError("reminder policy not found")
            if expected_revision is not None and current["revision"] != expected_revision:
                raise ValueError("reminder policy changed; reload it")
            new_mode = mode or current["mode"]
            new_days = days_mask if days_mask is not None else current["days_mask"]
            new_start = (
                _time_value(window_start) if window_start is not None else current["window_start"]
            )
            new_end = _time_value(window_end) if window_end is not None else current["window_end"]
            new_interval = (
                target_interval_minutes
                if target_interval_minutes is not None
                else current["target_interval_minutes"]
            )
            validate_policy_values(
                days_mask=new_days,
                window_start=new_start,
                window_end=new_end,
                target_interval_minutes=new_interval,
            )
            result = await conn.execute(
                """UPDATE reminder_policies SET mode = %s, days_mask = %s,
                          window_start = %s, window_end = %s,
                          target_interval_minutes = %s, revision = revision + 1,
                          planning_generation = planning_generation + 1, updated_at = now()
                   WHERE user_id = %s RETURNING *""",
                (new_mode, new_days, new_start, new_end, new_interval, user_id),
            )
            updated = await result.fetchone()
            await conn.execute(
                """UPDATE deliveries SET status = 'cancelled',
                          skip_reason = 'policy_changed'
                   WHERE user_id = %s AND kind = 'push'
                     AND status IN ('scheduled', 'claimed')""",
                (user_id,),
            )
            if updated["mode"] == "smart":
                await conn.execute(
                    """INSERT INTO reminder_refresh_requests(
                           user_id, requested_revision, requested_generation
                       ) VALUES (%s, %s, %s)
                       ON CONFLICT(user_id) DO UPDATE SET
                           requested_revision = EXCLUDED.requested_revision,
                           requested_generation = EXCLUDED.requested_generation,
                           requested_at = now()""",
                    (user_id, updated["revision"], updated["planning_generation"]),
                )
            else:
                await conn.execute(
                    "DELETE FROM reminder_refresh_requests WHERE user_id = %s",
                    (user_id,),
                )
            updated["timezone"] = current["timezone"]
            return updated


async def request_replan(database: db.Database, user_id: int) -> dict[str, Any]:
    async with database.connection() as conn:
        async with conn.transaction():
            policy = await _policy_row(conn, user_id, lock=True)
            if not policy:
                raise LookupError("reminder policy not found")
            if policy["mode"] != "smart":
                await conn.execute(
                    "DELETE FROM reminder_refresh_requests WHERE user_id = %s", (user_id,)
                )
                return policy
            result = await conn.execute(
                """UPDATE reminder_policies
                   SET planning_generation = planning_generation + 1, updated_at = now()
                   WHERE user_id = %s RETURNING *""",
                (user_id,),
            )
            updated = await result.fetchone()
            await conn.execute(
                """UPDATE deliveries SET status = 'cancelled', skip_reason = 'replanned'
                   WHERE user_id = %s AND kind = 'push' AND status = 'scheduled'
                     AND scheduled_for >= now() + %s""",
                (user_id, FREEZE_WINDOW),
            )
            if updated["mode"] == "smart":
                await conn.execute(
                    """INSERT INTO reminder_refresh_requests(
                           user_id, requested_revision, requested_generation
                       ) VALUES (%s, %s, %s)
                       ON CONFLICT(user_id) DO UPDATE SET
                           requested_revision = EXCLUDED.requested_revision,
                           requested_generation = EXCLUDED.requested_generation,
                           requested_at = now()""",
                    (user_id, updated["revision"], updated["planning_generation"]),
                )
            updated["timezone"] = policy["timezone"]
            return updated


async def needs_refresh(database: db.Database, user_id: int, *, now: datetime | None = None) -> bool:
    now = now or datetime.now(UTC)
    async with database.connection() as conn:
        policy = await _policy_row(conn, user_id)
        if not policy or policy["mode"] != "smart":
            return False
        request = await db.fetch_one(
            conn, "SELECT 1 FROM reminder_refresh_requests WHERE user_id = %s", (user_id,)
        )
        if request:
            return True
        plan = await db.fetch_one(
            conn,
            """SELECT horizon_end FROM reminder_plans
               WHERE user_id = %s AND policy_revision = %s AND status = 'accepted'
               ORDER BY planning_generation DESC LIMIT 1""",
            (user_id, policy["revision"]),
        )
    return not plan or plan["horizon_end"] < now + timedelta(hours=24)


async def ensure_refresh(database: db.Database, user_id: int) -> None:
    if not await needs_refresh(database, user_id):
        return
    async with database.connection() as conn:
        async with conn.transaction():
            policy = await _policy_row(conn, user_id, lock=True)
            if not policy or policy["mode"] != "smart":
                return
            existing = await db.fetch_one(
                conn, "SELECT 1 FROM reminder_refresh_requests WHERE user_id = %s", (user_id,)
            )
            if existing:
                return
            updated = await conn.execute(
                """UPDATE reminder_policies SET planning_generation = planning_generation + 1
                   WHERE user_id = %s RETURNING revision, planning_generation""",
                (user_id,),
            )
            row = await updated.fetchone()
            await conn.execute(
                """INSERT INTO reminder_refresh_requests(
                       user_id, requested_revision, requested_generation
                   ) VALUES (%s, %s, %s)""",
                (user_id, row["revision"], row["planning_generation"]),
            )


async def due_forecast(
    conn: AsyncConnection[dict[str, Any]], user_id: int, horizon_end: datetime
) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        conn,
        """SELECT w.id AS word_id, w.lemma, p.due_at, p.reps, p.interval_days,
                  lr.correct AS last_review_correct
           FROM progress p JOIN words w ON w.id = p.word_id
           JOIN decks d ON d.id = w.deck_id
           LEFT JOIN LATERAL (
               SELECT correct FROM reviews r WHERE r.word_id = w.id
               ORDER BY r.created_at DESC LIMIT 1
           ) lr ON true
           WHERE w.user_id = %s AND w.card_status = 'active' AND NOT d.is_archive
             AND p.due_at <= %s
           ORDER BY p.due_at, w.id LIMIT 100""",
        (user_id, horizon_end),
    )
    return [
        row
        for row in rows
        if not (
            row["reps"] == 0
            and float(row["interval_days"]) == 0
            and row["last_review_correct"] is False
        )
    ]


def deterministic_directives(
    policy: dict[str, Any], due_rows: list[dict[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    if not due_rows:
        return []
    zone = ZoneInfo(policy["timezone"])
    horizon_end = now + PLANNING_HORIZON
    earliest = _rounded_up(now.astimezone(zone) + FREEZE_WINDOW)
    directives: list[dict[str, Any]] = []
    day = earliest.date() - timedelta(days=1)
    final_day = horizon_end.astimezone(zone).date()
    last: datetime | None = None
    while day <= final_day:
        if weekday_enabled(policy["days_mask"], day):
            start, end = policy_window(policy, day)
            candidate = _rounded_up(max(start, earliest))
            count = 0
            while candidate < end and candidate.astimezone(UTC) <= horizon_end:
                if last is None or candidate.astimezone(UTC) - last >= timedelta(
                    minutes=policy["target_interval_minutes"]
                ):
                    eligible = [
                        row["word_id"]
                        for row in due_rows
                        if row["due_at"] is None or row["due_at"] <= candidate.astimezone(UTC)
                    ][:5]
                    if eligible:
                        directives.append(
                            {
                                "local_date": candidate.date().isoformat(),
                                "local_time": candidate.strftime("%H:%M"),
                                "word_ids": eligible,
                                "text": "Пора повторить слова. Это займёт пару минут.",
                            }
                        )
                        last = candidate.astimezone(UTC)
                        count += 1
                if count >= daily_cap(policy):
                    break
                candidate += timedelta(minutes=policy["target_interval_minutes"])
        day += timedelta(days=1)
    return directives


def _parse_directive_local(policy: dict[str, Any], directive: dict[str, Any]) -> datetime:
    local_day = date.fromisoformat(str(directive["local_date"]))
    local_time = time.fromisoformat(str(directive["local_time"]))
    if local_time.minute % 5 or local_time.second or local_time.microsecond:
        raise ValueError("curator reminder time must be rounded to five minutes")
    return _resolve_local(datetime.combine(local_day, local_time), ZoneInfo(policy["timezone"]))


async def materialize_plan(
    database: db.Database,
    user_id: int,
    directives: Iterable[dict[str, Any]],
    *,
    source: str,
    suppression_reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    if source not in {"curator", "deterministic"}:
        raise ValueError("invalid reminder plan source")
    if suppression_reason not in ALLOWED_SUPPRESSION_REASONS | {None}:
        suppression_reason = None
    raw_directives = list(directives)
    now = now or datetime.now(UTC)
    horizon_end = now + PLANNING_HORIZON
    async with database.connection() as conn:
        async with conn.transaction():
            policy = await _policy_row(conn, user_id, lock=True)
            if not policy or policy["mode"] != "smart":
                await conn.execute(
                    "DELETE FROM reminder_refresh_requests WHERE user_id = %s", (user_id,)
                )
                return None
            existing_plan = await db.fetch_one(
                conn,
                """SELECT * FROM reminder_plans
                   WHERE user_id = %s AND policy_revision = %s
                     AND planning_generation = %s""",
                (user_id, policy["revision"], policy["planning_generation"]),
            )
            if existing_plan:
                count = await db.fetch_one(
                    conn,
                    """SELECT count(*)::int AS count FROM deliveries
                       WHERE reminder_plan_id = %s""",
                    (existing_plan["id"],),
                )
                existing_plan["deliveries"] = count["count"]
                return existing_plan
            due_rows = await due_forecast(conn, user_id, horizon_end)
            due_by_word = {row["word_id"]: row["due_at"] for row in due_rows}
            existing = await db.fetch_all(
                conn,
                """SELECT scheduled_for, claimed_at, sent_at FROM deliveries
                   WHERE user_id = %s AND kind = 'push'
                     AND status IN ('sent', 'claimed', 'scheduled')
                     AND (status <> 'scheduled' OR scheduled_for < %s)
                   ORDER BY scheduled_for NULLS LAST, claimed_at""",
                (user_id, now + FREEZE_WINDOW),
            )
            protected = [
                row["sent_at"] or row["claimed_at"] or row["scheduled_for"]
                for row in existing
                if row["sent_at"] or row["claimed_at"] or row["scheduled_for"]
            ]
            accepted: list[dict[str, Any]] = []
            per_day: dict[date, int] = {}
            for raw in raw_directives:
                try:
                    local_value = _parse_directive_local(policy, raw)
                except (KeyError, TypeError, ValueError):
                    continue
                scheduled_for = local_value.astimezone(UTC)
                if not now + FREEZE_WINDOW <= scheduled_for <= horizon_end:
                    continue
                policy_day = containing_policy_day(policy, local_value)
                if policy_day is None:
                    continue
                if per_day.get(policy_day, 0) >= daily_cap(policy):
                    continue
                if any(
                    abs((scheduled_for - item).total_seconds())
                    < policy["target_interval_minutes"] * 60
                    for item in [*protected, *(row["scheduled_for"] for row in accepted)]
                ):
                    continue
                word_ids: list[int] = []
                for value in raw.get("word_ids", []):
                    try:
                        word_id = int(value)
                    except (TypeError, ValueError):
                        continue
                    if word_id in due_by_word and (
                        due_by_word[word_id] is None or due_by_word[word_id] <= scheduled_for
                    ):
                        word_ids.append(word_id)
                    if len(word_ids) == 5:
                        break
                if not word_ids:
                    word_ids = [
                        row["word_id"]
                        for row in due_rows
                        if row["due_at"] is None or row["due_at"] <= scheduled_for
                    ][:5]
                if not word_ids:
                    continue
                accepted.append(
                    {
                        "scheduled_for": scheduled_for,
                        "policy_day": policy_day,
                        "payload": {
                            "word_ids": word_ids,
                            "source": source,
                            "text": str(raw.get("text") or "")[:500],
                        },
                    }
                )
                per_day[policy_day] = per_day.get(policy_day, 0) + 1
            accepted.sort(key=lambda item: item["scheduled_for"])
            validated_suppression: str | None = None
            if suppression_reason == "no_due" and not due_rows:
                validated_suppression = suppression_reason
            elif suppression_reason == "low_due_load" and len(due_rows) <= 1:
                validated_suppression = suppression_reason
            elif suppression_reason == "active_session":
                active = await db.fetch_one(
                    conn,
                    "SELECT id FROM sessions WHERE user_id = %s AND status = 'open' LIMIT 1",
                    (user_id,),
                )
                validated_suppression = suppression_reason if active else None
            elif suppression_reason == "recent_practice":
                recent = await db.fetch_one(
                    conn,
                    """SELECT id FROM reviews WHERE user_id = %s
                       AND created_at >= %s LIMIT 1""",
                    (user_id, now - timedelta(minutes=30)),
                )
                validated_suppression = suppression_reason if recent else None
            elif suppression_reason == "recently_ignored":
                ignored = await db.fetch_one(
                    conn,
                    """SELECT count(*)::int AS count FROM deliveries
                       WHERE user_id = %s AND kind = 'push' AND status = 'sent'
                         AND sent_at >= %s""",
                    (user_id, now - timedelta(days=3)),
                )
                recent_review = await db.fetch_one(
                    conn,
                    """SELECT id FROM reviews WHERE user_id = %s
                       AND created_at >= %s LIMIT 1""",
                    (user_id, now - timedelta(days=3)),
                )
                if ignored and ignored["count"] >= 2 and not recent_review:
                    validated_suppression = suppression_reason
            fallback = deterministic_directives(policy, due_rows, now=now)
            cadence_deadline = (
                _parse_directive_local(policy, fallback[0]).astimezone(UTC)
                + timedelta(minutes=policy["target_interval_minutes"])
                if fallback
                else now + timedelta(minutes=policy["target_interval_minutes"])
            )
            if (
                source == "curator"
                and due_rows
                and validated_suppression is None
                and (
                    not accepted
                    or (fallback and accepted[0]["scheduled_for"] > cadence_deadline)
                )
            ):
                raise ValueError("curator plan violates cadence floor")
            await conn.execute(
                """UPDATE reminder_plans SET status = 'superseded'
                   WHERE user_id = %s AND status = 'accepted'""",
                (user_id,),
            )
            plan_result = await conn.execute(
                """INSERT INTO reminder_plans(
                       user_id, policy_revision, planning_generation, source,
                       horizon_start, horizon_end, suppression_reason, payload
                   ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   RETURNING *""",
                (
                    user_id,
                    policy["revision"],
                    policy["planning_generation"],
                    source,
                    now,
                    horizon_end,
                    validated_suppression,
                    Jsonb({"directives": raw_directives}),
                ),
            )
            plan = await plan_result.fetchone()
            await conn.execute(
                """UPDATE deliveries SET status = 'cancelled', skip_reason = 'superseded'
                   WHERE user_id = %s AND kind = 'push' AND status = 'scheduled'
                     AND scheduled_for >= %s""",
                (user_id, now + FREEZE_WINDOW),
            )
            for item in accepted:
                key = (
                    f"reminder:{user_id}:{policy['revision']}:"
                    f"{item['scheduled_for'].isoformat()}"
                )
                await conn.execute(
                    """INSERT INTO deliveries(
                           user_id, kind, idempotency_key, status, payload,
                           scheduled_for, reminder_plan_id, reminder_revision, source
                       ) VALUES (%s, 'push', %s, 'scheduled', %s, %s, %s, %s, %s)
                       ON CONFLICT(idempotency_key) DO UPDATE SET
                           status = CASE WHEN deliveries.status = 'cancelled'
                                         THEN 'scheduled' ELSE deliveries.status END,
                           payload = EXCLUDED.payload,
                           reminder_plan_id = EXCLUDED.reminder_plan_id,
                           source = EXCLUDED.source,
                           skip_reason = NULL""",
                    (
                        user_id,
                        key,
                        Jsonb(item["payload"]),
                        item["scheduled_for"],
                        plan["id"],
                        policy["revision"],
                        source,
                    ),
                )
            await conn.execute(
                """DELETE FROM reminder_refresh_requests
                   WHERE user_id = %s AND requested_revision = %s
                     AND requested_generation = %s""",
                (user_id, policy["revision"], policy["planning_generation"]),
            )
            plan["deliveries"] = len(accepted)
            return plan


async def materialize_deterministic(
    database: db.Database, user_id: int, *, now: datetime | None = None
) -> dict[str, Any] | None:
    now = now or datetime.now(UTC)
    async with database.connection() as conn:
        policy = await _policy_row(conn, user_id)
        if not policy or policy["mode"] != "smart":
            return None
        due_rows = await due_forecast(conn, user_id, now + PLANNING_HORIZON)
    directives = deterministic_directives(policy, due_rows, now=now)
    reason = "no_due" if not due_rows else None
    return await materialize_plan(
        database,
        user_id,
        directives,
        source="deterministic",
        suppression_reason=reason,
        now=now,
    )


async def list_upcoming(
    database: db.Database, user_id: int, *, limit: int = 8
) -> list[dict[str, Any]]:
    async with database.connection() as conn:
        return await db.fetch_all(
            conn,
            """SELECT id, scheduled_for, source, status, skip_reason
               FROM deliveries WHERE user_id = %s AND kind = 'push'
                 AND status = 'scheduled' AND scheduled_for >= now()
               ORDER BY scheduled_for LIMIT %s""",
            (user_id, limit),
        )


async def last_outcome(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        return await db.fetch_one(
            conn,
            """SELECT status, sent_at, scheduled_for, skip_reason, error
               FROM deliveries WHERE user_id = %s AND kind = 'push'
                 AND status IN ('sent', 'skipped', 'failed', 'cancelled')
               ORDER BY coalesce(sent_at, claimed_at, scheduled_for) DESC LIMIT 1""",
            (user_id,),
        )


async def claim_due_delivery(
    database: db.Database,
    user_id: int | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Claim one planned reminder after revalidating every effective policy guard."""
    now = now or datetime.now(UTC)
    async with database.connection() as conn:
        async with conn.transaction():
            owner_clause = " AND d.user_id = %s" if user_id is not None else ""
            params: tuple[Any, ...] = (now, user_id) if user_id is not None else (now,)
            result = await conn.execute(
                """SELECT d.*, rp.mode, rp.days_mask, rp.window_start, rp.window_end,
                          rp.target_interval_minutes, rp.revision AS current_revision,
                          u.timezone
                   FROM deliveries d
                   JOIN reminder_policies rp ON rp.user_id = d.user_id
                   JOIN users u ON u.id = d.user_id
                   WHERE d.kind = 'push' AND d.status = 'scheduled'
                     AND d.scheduled_for <= %s"""
                + owner_clause
                + " ORDER BY d.scheduled_for, d.id FOR UPDATE OF d SKIP LOCKED LIMIT 1",
                params,
            )
            delivery = await result.fetchone()
            if not delivery:
                return None

            async def skip(reason: str, *, cancelled: bool = False) -> None:
                await conn.execute(
                    """UPDATE deliveries SET status = %s, skip_reason = %s
                       WHERE id = %s""",
                    ("cancelled" if cancelled else "skipped", reason, delivery["id"]),
                )

            if (
                delivery["mode"] != "smart"
                or delivery["reminder_revision"] != delivery["current_revision"]
            ):
                await skip("policy_changed", cancelled=True)
                return None
            if now - delivery["scheduled_for"] > EXECUTION_GRACE:
                await skip("missed_execution_window")
                return None
            local_now = now.astimezone(ZoneInfo(delivery["timezone"]))
            if containing_policy_day(delivery, local_now) is None:
                await skip("outside_policy", cancelled=True)
                return None
            active = await db.fetch_one(
                conn,
                """SELECT id FROM sessions WHERE user_id = %s AND kind = 'long'
                   AND status = 'open' LIMIT 1""",
                (delivery["user_id"],),
            )
            if active:
                await skip("active_session")
                return None
            recent = await db.fetch_one(
                conn,
                """SELECT id FROM reviews WHERE user_id = %s
                   AND created_at >= %s ORDER BY created_at DESC LIMIT 1""",
                (delivery["user_id"], now - timedelta(minutes=30)),
            )
            if recent:
                await skip("recent_practice")
                return None
            last = await db.fetch_one(
                conn,
                """SELECT coalesce(sent_at, claimed_at) AS occurred_at
                   FROM deliveries WHERE user_id = %s AND kind = 'push' AND id <> %s
                     AND status IN ('claimed', 'sent')
                   ORDER BY coalesce(sent_at, claimed_at) DESC LIMIT 1""",
                (delivery["user_id"], delivery["id"]),
            )
            if last and now - last["occurred_at"] < timedelta(
                minutes=delivery["target_interval_minutes"]
            ):
                await skip("minimum_gap")
                return None
            due_rows = await due_forecast(conn, delivery["user_id"], now)
            if not due_rows:
                await skip("no_due")
                return None
            due_ids = {row["word_id"] for row in due_rows}
            payload = _object(delivery["payload"])
            selected: list[int] = []
            for value in payload.get("word_ids", []):
                try:
                    word_id = int(value)
                except (TypeError, ValueError):
                    continue
                if word_id in due_ids:
                    selected.append(word_id)
                if len(selected) == 5:
                    break
            payload["word_ids"] = selected or list(due_ids)[:5]
            claimed = await conn.execute(
                """UPDATE deliveries SET status = 'claimed', claimed_at = %s, payload = %s
                   WHERE id = %s AND status = 'scheduled' RETURNING *""",
                (now, Jsonb(payload), delivery["id"]),
            )
            return await claimed.fetchone()


async def authorize_claimed_delivery(
    database: db.Database, delivery_id: int, *, now: datetime | None = None
) -> bool:
    """Revalidate a claim immediately before the external Telegram side effect."""
    now = now or datetime.now(UTC)
    async with database.connection() as conn:
        async with conn.transaction():
            result = await conn.execute(
                """SELECT d.id, d.status, d.scheduled_for, d.reminder_revision,
                          rp.mode, rp.revision, rp.days_mask, rp.window_start,
                          rp.window_end, rp.target_interval_minutes, u.timezone
                   FROM deliveries d
                   JOIN reminder_policies rp ON rp.user_id = d.user_id
                   JOIN users u ON u.id = d.user_id
                   WHERE d.id = %s AND d.kind = 'push' FOR UPDATE OF d""",
                (delivery_id,),
            )
            delivery = await result.fetchone()
            if not delivery or delivery["status"] != "claimed":
                return False
            local_now = now.astimezone(ZoneInfo(delivery["timezone"]))
            if (
                delivery["mode"] != "smart"
                or delivery["reminder_revision"] != delivery["revision"]
                or containing_policy_day(delivery, local_now) is None
            ):
                await conn.execute(
                    """UPDATE deliveries SET status = 'cancelled',
                              skip_reason = 'policy_changed'
                       WHERE id = %s AND status = 'claimed'""",
                    (delivery_id,),
                )
                return False
            return True


async def set_pending_action(
    database: db.Database,
    user_id: int,
    kind: str,
    payload: dict[str, Any],
    *,
    ttl: timedelta = timedelta(minutes=10),
) -> None:
    async with database.connection() as conn:
        await conn.execute(
            """INSERT INTO telegram_pending_actions(user_id, kind, payload, expires_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT(user_id) DO UPDATE SET kind = EXCLUDED.kind,
                   payload = EXCLUDED.payload, expires_at = EXCLUDED.expires_at,
                   created_at = now()""",
            (user_id, kind, Jsonb(payload), datetime.now(UTC) + ttl),
        )


async def get_pending_action(database: db.Database, user_id: int) -> dict[str, Any] | None:
    async with database.connection() as conn:
        row = await db.fetch_one(
            conn,
            """DELETE FROM telegram_pending_actions
               WHERE user_id = %s AND expires_at <= now() RETURNING *""",
            (user_id,),
        )
        if row:
            return None
        row = await db.fetch_one(
            conn, "SELECT * FROM telegram_pending_actions WHERE user_id = %s", (user_id,)
        )
    if row:
        row["payload"] = _object(row["payload"])
    return row


async def clear_pending_action(database: db.Database, user_id: int) -> None:
    async with database.connection() as conn:
        await conn.execute(
            "DELETE FROM telegram_pending_actions WHERE user_id = %s", (user_id,)
        )
