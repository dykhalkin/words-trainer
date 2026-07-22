from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from pydantic import SecretStr

from bot.config import Settings
from bot.curator import CuratorPlanOutput, CuratorService
from tests.support import temporary_database
from vocab import curator, db, reminders, scheduler, words
from vocab.models import Word


def make_word(lemma: str, translation: str) -> Word:
    return Word(
        lemma=lemma,
        kind="other",
        translation=translation,
        example=f"Das ist {lemma}.",
        pronunciation=lemma,
        source_file="test",
    )


class CuratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_analysis_is_scoped_and_failed_later_run_invalidates_plan(self) -> None:
        async with temporary_database() as database:
            owner = await words.bootstrap_user(database, name="owner", chat_id=101)
            spouse = await words.bootstrap_user(database, name="spouse", chat_id=202)
            owner_deck = await words.create_deck(database, owner["id"], "de", "Core")
            spouse_deck = await words.create_deck(database, spouse["id"], "de", "Core")
            owner_word, _ = await words.add_word(
                database,
                user_id=owner["id"], language="de", deck_id=owner_deck["id"],
                word=make_word("eins", "один"),
            )
            spouse_word, _ = await words.add_word(
                database,
                user_id=spouse["id"], language="de", deck_id=spouse_deck["id"],
                word=make_word("geheim", "секрет"),
            )
            owner_task = await scheduler.create_task(database, owner["id"], word_id=owner_word["id"])
            spouse_task = await scheduler.create_task(database, spouse["id"], word_id=spouse_word["id"])
            await scheduler.submit_answer(database, owner["id"], owner_task["task_id"], "один")
            await scheduler.submit_answer(database, spouse["id"], spouse_task["task_id"], "wrong")
            async with database.connection() as conn:
                await conn.execute(
                    "UPDATE progress SET due_at = now() - interval '1 hour' WHERE word_id = %s",
                    (owner_word["id"],),
                )

            analysis = await curator.analyze(database, owner["id"])
            self.assertEqual({row["word_id"] for row in analysis["hard_words"]}, {owner_word["id"]})
            self.assertNotIn("geheim", str(analysis))
            await curator.save_plan(
                database,
                owner["id"],
                "plan",
                {"focus": [{"word_id": owner_word["id"], "reason": "practice"}], "digest": "ok"},
            )
            self.assertIsNotNone(await curator.fresh_plan(database, owner["id"]))
            async with database.connection() as conn:
                await conn.execute(
                    """INSERT INTO curator_runs(user_id, kind, status, error)
                       VALUES (%s, 'plan', 'failed', 'mock outage')""",
                    (owner["id"],),
                )
            self.assertIsNone(await curator.fresh_plan(database, owner["id"]))
            first = await scheduler.claim_digest(
                database, owner["id"], "2026-07-20", {"digest": "weekly"}
            )
            second = await scheduler.claim_digest(
                database, owner["id"], "2026-07-20", {"digest": "weekly"}
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)

    async def test_unavailable_or_invalid_curator_falls_back_inside_learner_policy(self) -> None:
        async with temporary_database() as database:
            owner = await words.bootstrap_user(database, name="owner", chat_id=303)
            spouse = await words.bootstrap_user(database, name="spouse", chat_id=404)
            deck = await words.create_deck(database, owner["id"], "de", "Core")
            word, _ = await words.add_word(
                database,
                user_id=owner["id"],
                language="de",
                deck_id=deck["id"],
                word=make_word("fällig", "просроченный"),
            )
            now = datetime.now(timezone.utc)
            async with database.connection() as conn:
                await conn.execute(
                    """INSERT INTO progress(word_id, reps, interval_days, due_at)
                       VALUES (%s, 3, 7, %s)""",
                    (word["id"], now - timedelta(minutes=1)),
                )
            current = await reminders.get_policy(database, owner["id"])
            await reminders.update_policy(
                database,
                owner["id"],
                expected_revision=current["revision"],
                mode="smart",
                window_start="00:00",
                window_end="23:59",
                target_interval_minutes=180,
            )
            base = dict(
                TELEGRAM_BOT_TOKEN="token",
                DATABASE_URL="postgresql://unused",
                OWNER_CHAT_ID=owner["chat_id"],
                SPOUSE_CHAT_ID=spouse["chat_id"],
            )
            unavailable = CuratorService(Settings(**base))
            await unavailable.run_for(database, owner)
            async with database.connection() as conn:
                fallback = await db.fetch_one(
                    conn,
                    """SELECT * FROM reminder_plans WHERE user_id = %s
                       ORDER BY id DESC LIMIT 1""",
                    (owner["id"],),
                )
            self.assertEqual(fallback["source"], "deterministic")

            await reminders.request_replan(database, owner["id"])
            calls: list[dict] = []

            class FakeResult:
                context_wrapper = SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=10, output_tokens=10)
                )

                def final_output_as(self, output_type, *, raise_if_incorrect_type):
                    return CuratorPlanOutput(
                        focus=[],
                        reminders=[
                            {
                                "local_date": "2000-01-01",
                                "local_time": "12:00",
                                "word_ids": [999999],
                                "text": "invalid",
                            }
                        ],
                        suppression_reason=None,
                    )

            async def runner(*args, **kwargs):
                calls.append(json.loads(args[1]))
                return FakeResult()

            configured = Settings(
                **base,
                OPENAI_API_KEY=SecretStr("test-key"),
                CURATOR_MODEL="curator-test",
            )
            await CuratorService(configured, runner=runner).run_for(database, owner)
            self.assertEqual(calls[0]["user_id"], owner["id"])
            self.assertEqual(calls[0]["reminder_policy"]["user_id"], owner["id"])
            constraints = calls[0]["planning_constraints"]
            self.assertEqual(constraints["minimum_gap_minutes"], 180)
            self.assertEqual(constraints["maximum_sends_per_policy_day"], 6)
            self.assertGreater(
                constraints["horizon_end_utc"], constraints["horizon_start_utc"]
            )
            self.assertGreater(
                constraints["freeze_until_utc"], constraints["horizon_start_utc"]
            )
            self.assertEqual(calls[0]["eligible_word_ids"], [word["id"]])
            async with database.connection() as conn:
                latest = await db.fetch_one(
                    conn,
                    """SELECT * FROM reminder_plans WHERE user_id = %s
                       ORDER BY id DESC LIMIT 1""",
                    (owner["id"],),
                )
                delivery = await db.fetch_one(
                    conn,
                    """SELECT * FROM deliveries WHERE user_id = %s AND status = 'scheduled'
                       ORDER BY scheduled_for LIMIT 1""",
                    (owner["id"],),
                )
            self.assertEqual(latest["source"], "deterministic")
            policy_row = await reminders.get_policy(database, owner["id"])
            local = delivery["scheduled_for"].astimezone(ZoneInfo(policy_row["timezone"]))
            self.assertIsNotNone(reminders.containing_policy_day(policy_row, local))


if __name__ == "__main__":
    unittest.main()
