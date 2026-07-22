from __future__ import annotations

import asyncio
import json
import unittest
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from bot.reminders import handle_pending_text
from tests.support import temporary_database
from vocab import db, reminders, scheduler, words
from vocab.models import Word


UTC = timezone.utc


def make_word(lemma: str, translation: str) -> Word:
    return Word(
        lemma=lemma,
        kind="other",
        translation=translation,
        example=f"Das ist {lemma}.",
        pronunciation=lemma,
        source_file="test",
    )


def policy(**overrides):
    value = {
        "timezone": "Europe/Berlin",
        "days_mask": 127,
        "window_start": time(9),
        "window_end": time(21),
        "target_interval_minutes": 180,
    }
    value.update(overrides)
    return value


class ReminderTimeTests(unittest.TestCase):
    def test_same_day_overnight_caps_and_policy_day(self) -> None:
        same_day = policy()
        self.assertEqual(reminders.window_minutes(same_day), 12 * 60)
        self.assertEqual(reminders.daily_cap(same_day), 4)
        overnight = policy(window_start=time(22), window_end=time(1), days_mask=1 << 4)
        self.assertEqual(reminders.window_minutes(overnight), 180)
        self.assertEqual(reminders.daily_cap(overnight), 1)
        local = datetime(2026, 7, 25, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))
        self.assertEqual(reminders.containing_policy_day(overnight, local), date(2026, 7, 24))
        self.assertIsNone(
            reminders.containing_policy_day(
                overnight,
                datetime(2026, 7, 25, 1, 0, tzinfo=ZoneInfo("Europe/Berlin")),
            )
        )

    def test_dst_gap_is_advanced_and_fall_overlap_uses_first_fold(self) -> None:
        spring = policy(window_start=time(2, 30), window_end=time(4))
        start, end = reminders.policy_window(spring, date(2026, 3, 29))
        self.assertEqual(start.strftime("%H:%M"), "03:00")
        self.assertLess(start, end)

        fall = policy(window_start=time(1), window_end=time(4))
        parsed = reminders._parse_directive_local(
            fall, {"local_date": "2026-10-25", "local_time": "02:30"}
        )
        self.assertEqual(parsed.fold, 0)
        self.assertEqual(parsed.utcoffset(), timedelta(hours=2))

    def test_deterministic_plan_stays_inside_window_cadence_and_cap(self) -> None:
        configured = policy()
        now = datetime(2026, 7, 23, 6, 0, tzinfo=UTC)
        due = [{"word_id": 1, "due_at": now - timedelta(days=1)}]
        directives = reminders.deterministic_directives(configured, due, now=now)
        self.assertTrue(directives)
        self.assertLessEqual(len(directives), 8)  # at most four on each policy day
        instants = [
            reminders._parse_directive_local(configured, item).astimezone(UTC)
            for item in directives
        ]
        self.assertTrue(
            all(
                later - earlier >= timedelta(hours=3)
                for earlier, later in zip(instants, instants[1:])
            )
        )
        self.assertTrue(
            all(
                reminders.containing_policy_day(
                    configured, instant.astimezone(ZoneInfo("Europe/Berlin"))
                )
                is not None
                for instant in instants
            )
        )


class ReminderPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=501
        )
        self.spouse = await words.bootstrap_user(
            self.database, name="spouse", chat_id=502
        )
        self.deck = await words.create_deck(
            self.database, self.owner["id"], "de", "Reminder"
        )
        self.word, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=make_word("wiederholen", "повторять"),
        )

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def _due(self, when: datetime) -> None:
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 3, 7, %s)
                   ON CONFLICT(word_id) DO UPDATE SET reps = 3, interval_days = 7,
                       due_at = EXCLUDED.due_at""",
                (self.word["id"], when),
            )

    async def _enable(self, *, start="09:00", end="21:00", cadence=180) -> dict:
        current = await reminders.get_policy(self.database, self.owner["id"])
        return await reminders.update_policy(
            self.database,
            self.owner["id"],
            expected_revision=current["revision"],
            mode="smart",
            window_start=start,
            window_end=end,
            target_interval_minutes=cadence,
        )

    async def _materialize(
        self, now: datetime, scheduled: datetime, *, source="curator", word_ids=None
    ) -> dict | None:
        local = scheduled.astimezone(ZoneInfo("Europe/Berlin"))
        return await reminders.materialize_plan(
            self.database,
            self.owner["id"],
            [
                {
                    "local_date": local.date().isoformat(),
                    "local_time": local.strftime("%H:%M"),
                    "word_ids": word_ids if word_ids is not None else [self.word["id"]],
                    "text": "Пора повторить",
                }
            ],
            source=source,
            now=now,
        )

    async def test_new_user_is_off_and_policy_updates_are_isolated_revisioned_and_cancel(self) -> None:
        owner = await reminders.get_policy(self.database, self.owner["id"])
        spouse = await reminders.get_policy(self.database, self.spouse["id"])
        self.assertEqual(owner["mode"], "off")
        self.assertEqual((owner["window_start"], owner["window_end"]), (time(9), time(21)))
        self.assertEqual(owner["target_interval_minutes"], 180)
        updated = await self._enable(start="00:00", end="23:59")
        self.assertEqual(updated["revision"], owner["revision"] + 1)
        self.assertEqual(updated["planning_generation"], owner["planning_generation"] + 1)
        self.assertEqual((await reminders.get_policy(self.database, self.spouse["id"]))["revision"], spouse["revision"])
        async with self.database.connection() as conn:
            refresh = await db.fetch_one(
                conn, "SELECT * FROM reminder_refresh_requests WHERE user_id = %s", (self.owner["id"],)
            )
        self.assertEqual(refresh["requested_revision"], updated["revision"])

        now = datetime.now(UTC).replace(second=0, microsecond=0)
        await self._due(now - timedelta(minutes=1))
        scheduled = now + timedelta(minutes=25)
        scheduled += timedelta(minutes=(-scheduled.minute) % 5)
        await self._materialize(now, scheduled, source="deterministic")
        changed = await reminders.update_policy(
            self.database,
            self.owner["id"],
            expected_revision=updated["revision"],
            window_start="10:00",
        )
        async with self.database.connection() as conn:
            delivery = await db.fetch_one(
                conn, "SELECT status, skip_reason FROM deliveries WHERE user_id = %s", (self.owner["id"],)
            )
        self.assertEqual(changed["revision"], updated["revision"] + 1)
        self.assertEqual((delivery["status"], delivery["skip_reason"]), ("cancelled", "policy_changed"))

    async def test_materializer_enforces_allowlist_window_gap_cap_and_idempotency(self) -> None:
        await self._enable()
        now = datetime(2026, 7, 23, 7, 0, tzinfo=UTC)  # 09:00 Berlin
        await self._due(now - timedelta(minutes=1))
        valid = now + timedelta(minutes=20)
        local = valid.astimezone(ZoneInfo("Europe/Berlin"))
        directives = [
            {
                "local_date": local.date().isoformat(),
                "local_time": local.strftime("%H:%M"),
                "word_ids": [self.word["id"], 999999],
                "text": "valid",
            },
            {
                "local_date": local.date().isoformat(),
                "local_time": (local + timedelta(minutes=5)).strftime("%H:%M"),
                "word_ids": [self.word["id"]],
                "text": "too close",
            },
            {
                "local_date": local.date().isoformat(),
                "local_time": "23:00",
                "word_ids": [self.word["id"]],
                "text": "outside",
            },
        ]
        first = await reminders.materialize_plan(
            self.database, self.owner["id"], directives, source="curator", now=now
        )
        second = await reminders.materialize_plan(
            self.database, self.owner["id"], directives, source="curator", now=now
        )
        self.assertEqual(first["deliveries"], 1)
        self.assertEqual(second["deliveries"], 1)
        async with self.database.connection() as conn:
            rows = await db.fetch_all(
                conn, "SELECT * FROM deliveries WHERE user_id = %s", (self.owner["id"],)
            )
        self.assertEqual(len(rows), 1)
        payload = rows[0]["payload"]
        payload = json.loads(payload) if isinstance(payload, str) else payload
        self.assertEqual(payload["word_ids"], [self.word["id"]])

    async def test_frozen_delivery_survives_manual_replan_but_policy_change_cancels_it(self) -> None:
        enabled = await self._enable()
        now = datetime.now(UTC)
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO deliveries(
                       user_id, kind, idempotency_key, status, payload, scheduled_for,
                       reminder_revision, source
                   ) VALUES (%s, 'push', %s, 'scheduled', '{}'::jsonb, %s, %s, 'deterministic')""",
                (
                    self.owner["id"],
                    "frozen-test",
                    now + timedelta(minutes=10),
                    enabled["revision"],
                ),
            )
        replanned = await reminders.request_replan(self.database, self.owner["id"])
        async with self.database.connection() as conn:
            frozen = await db.fetch_one(
                conn, "SELECT status FROM deliveries WHERE idempotency_key = 'frozen-test'"
            )
        self.assertEqual(frozen["status"], "scheduled")
        await reminders.update_policy(
            self.database,
            self.owner["id"],
            expected_revision=replanned["revision"],
            mode="off",
        )
        async with self.database.connection() as conn:
            frozen = await db.fetch_one(
                conn, "SELECT status FROM deliveries WHERE idempotency_key = 'frozen-test'"
            )
            refresh = await db.fetch_one(
                conn, "SELECT 1 FROM reminder_refresh_requests WHERE user_id = %s", (self.owner["id"],)
            )
        self.assertEqual(frozen["status"], "cancelled")
        self.assertIsNone(refresh)

    async def test_claim_revalidates_due_state_session_recent_practice_and_missed_window(self) -> None:
        await self._enable(start="00:00", end="23:59", cadence=60)
        now = datetime.now(UTC).replace(second=0, microsecond=0)

        async def planned(suffix: int) -> tuple[datetime, int]:
            await self._due(now - timedelta(minutes=1))
            scheduled = now + timedelta(minutes=20 + 65 * suffix)
            scheduled = scheduled + timedelta(minutes=(-scheduled.minute) % 5)
            await reminders.request_replan(self.database, self.owner["id"])
            await self._materialize(now, scheduled, source="deterministic")
            async with self.database.connection() as conn:
                row = await db.fetch_one(
                    conn,
                    """SELECT id FROM deliveries WHERE user_id = %s AND status = 'scheduled'
                       ORDER BY scheduled_for DESC LIMIT 1""",
                    (self.owner["id"],),
                )
            return scheduled, row["id"]

        scheduled, delivery_id = await planned(0)
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = %s WHERE word_id = %s",
                (scheduled + timedelta(days=1), self.word["id"]),
            )
        self.assertIsNone(
            await reminders.claim_due_delivery(
                self.database, self.owner["id"], now=scheduled
            )
        )
        async with self.database.connection() as conn:
            skipped = await db.fetch_one(conn, "SELECT * FROM deliveries WHERE id = %s", (delivery_id,))
        self.assertEqual(skipped["skip_reason"], "no_due")

        scheduled, delivery_id = await planned(1)
        await scheduler.start_session(self.database, self.owner["id"], kind="long")
        self.assertIsNone(
            await reminders.claim_due_delivery(self.database, self.owner["id"], now=scheduled)
        )
        async with self.database.connection() as conn:
            active = await db.fetch_one(conn, "SELECT * FROM deliveries WHERE id = %s", (delivery_id,))
            await conn.execute(
                "UPDATE sessions SET status = 'stopped', ended_at = now() WHERE user_id = %s",
                (self.owner["id"],),
            )
        self.assertEqual(active["skip_reason"], "active_session")

        scheduled, delivery_id = await planned(2)
        task = await scheduler.create_task(
            self.database,
            self.owner["id"],
            word_id=self.word["id"],
            task_type="flashcard_ru_de",
        )
        await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "wiederholen"
        )
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = %s WHERE word_id = %s",
                (scheduled - timedelta(minutes=1), self.word["id"]),
            )
            await conn.execute(
                "UPDATE reviews SET created_at = %s WHERE task_id = %s",
                (scheduled - timedelta(minutes=5), task["task_id"]),
            )
        self.assertIsNone(
            await reminders.claim_due_delivery(self.database, self.owner["id"], now=scheduled)
        )
        async with self.database.connection() as conn:
            recent = await db.fetch_one(conn, "SELECT * FROM deliveries WHERE id = %s", (delivery_id,))
        self.assertEqual(recent["skip_reason"], "recent_practice")

        scheduled, delivery_id = await planned(3)
        self.assertIsNone(
            await reminders.claim_due_delivery(
                self.database,
                self.owner["id"],
                now=scheduled + reminders.EXECUTION_GRACE + timedelta(seconds=1),
            )
        )
        async with self.database.connection() as conn:
            missed = await db.fetch_one(conn, "SELECT * FROM deliveries WHERE id = %s", (delivery_id,))
        self.assertEqual(missed["skip_reason"], "missed_execution_window")

    async def test_concurrent_claim_and_pending_action_restart_expiry(self) -> None:
        await self._enable(start="00:00", end="23:59", cadence=60)
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        await self._due(now - timedelta(minutes=1))
        scheduled = now + timedelta(minutes=20)
        scheduled += timedelta(minutes=(-scheduled.minute) % 5)
        await self._materialize(now, scheduled, source="deterministic")
        claims = await asyncio.gather(
            reminders.claim_due_delivery(self.database, self.owner["id"], now=scheduled),
            reminders.claim_due_delivery(self.database, self.owner["id"], now=scheduled),
        )
        self.assertEqual(sum(item is not None for item in claims), 1)
        self.assertIsNone(
            await reminders.claim_due_delivery(
                self.database, self.owner["id"], now=scheduled + timedelta(minutes=1)
            )
        )

        await reminders.set_pending_action(
            self.database,
            self.owner["id"],
            "reminder_window_end",
            {"revision": 2, "window_start": "10:00"},
        )
        restored = await reminders.get_pending_action(self.database, self.owner["id"])
        self.assertEqual(restored["payload"]["window_start"], "10:00")
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE telegram_pending_actions SET expires_at = now() - interval '1 second' "
                "WHERE user_id = %s",
                (self.owner["id"],),
            )
        self.assertIsNone(
            await reminders.get_pending_action(self.database, self.owner["id"])
        )

    async def test_claimed_delivery_is_blocked_when_policy_changes_before_send(self) -> None:
        enabled = await self._enable(start="00:00", end="23:59", cadence=60)
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        await self._due(now - timedelta(minutes=1))
        scheduled = now + timedelta(minutes=20)
        scheduled += timedelta(minutes=(-scheduled.minute) % 5)
        await self._materialize(now, scheduled, source="deterministic")
        claimed = await reminders.claim_due_delivery(
            self.database, self.owner["id"], now=scheduled
        )
        self.assertIsNotNone(claimed)
        await reminders.update_policy(
            self.database,
            self.owner["id"],
            expected_revision=enabled["revision"],
            mode="off",
        )
        self.assertFalse(
            await reminders.authorize_claimed_delivery(
                self.database, claimed["id"], now=scheduled
            )
        )
        async with self.database.connection() as conn:
            row = await db.fetch_one(
                conn, "SELECT status, skip_reason FROM deliveries WHERE id = %s", (claimed["id"],)
            )
        self.assertEqual((row["status"], row["skip_reason"]), ("cancelled", "policy_changed"))

    async def test_typed_setting_survives_restart_and_replies_without_editing_user_message(self) -> None:
        current = await reminders.get_policy(self.database, self.owner["id"])
        await reminders.set_pending_action(
            self.database,
            self.owner["id"],
            "reminder_cadence",
            {"revision": current["revision"]},
        )
        message = SimpleNamespace(
            text="4",
            answer=AsyncMock(),
            edit_text=AsyncMock(),
        )
        handled = await handle_pending_text(message, self.database, self.owner)
        self.assertTrue(handled)
        message.answer.assert_awaited_once()
        message.edit_text.assert_not_awaited()
        pending = await reminders.get_pending_action(self.database, self.owner["id"])
        self.assertEqual(pending["kind"], "reminder_confirm")
        self.assertEqual(pending["payload"]["changes"]["target_interval_minutes"], 240)


if __name__ == "__main__":
    unittest.main()
