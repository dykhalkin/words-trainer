from __future__ import annotations

import asyncio
import random
import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tests.support import temporary_database
from vocab import db, reminders, scheduler, words
from vocab.models import Word


def make_word(lemma: str, translation: str) -> Word:
    return Word(
        lemma=lemma,
        kind="other",
        translation=translation,
        example=f"Das ist {lemma}.",
        pronunciation=lemma,
        source_file="test.csv",
    )


class SessionIntegrityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=1001
        )
        self.spouse = await words.bootstrap_user(
            self.database, name="spouse", chat_id=1002
        )
        self.deck = await words.create_deck(self.database, self.owner["id"], "de", "Core")
        self.word_rows = []
        for lemma, translation in [
            ("eins", "один"),
            ("zwei", "два"),
            ("drei", "три"),
            ("vier", "четыре"),
        ]:
            row, _ = await words.add_word(
                self.database,
                user_id=self.owner["id"],
                language="de",
                deck_id=self.deck["id"],
                word=make_word(lemma, translation),
            )
            self.word_rows.append(row)

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def test_task_answer_updates_progress_and_hides_expected_until_answered(self) -> None:
        task = await scheduler.create_task(
            self.database,
            self.owner["id"],
            rng=random.Random(1),
            word_query="eins",
        )
        open_context = await scheduler.task_context(
            self.database, self.owner["id"], task["task_id"]
        )
        self.assertNotIn("expected", open_context)
        result = await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "один"
        )
        reused = await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "один"
        )
        answered_context = await scheduler.task_context(
            self.database, self.owner["id"], task["task_id"]
        )
        self.assertTrue(result["correct"])
        self.assertEqual(result["stage"], 1)
        self.assertEqual(result["example"], "Das ist eins.")
        self.assertEqual(reused["error"], "exercise expired")
        self.assertIn("expected", answered_context)
        self.assertEqual(answered_context["answer"], "один")
        self.assertNotIn("example", answered_context["verdict"])

    async def test_concurrent_answers_commit_one_review_and_transition(self) -> None:
        task = await scheduler.create_task(
            self.database, self.owner["id"], word_query="eins"
        )
        results = await asyncio.gather(
            scheduler.submit_answer(
                self.database, self.owner["id"], task["task_id"], "один"
            ),
            scheduler.submit_answer(
                self.database, self.owner["id"], task["task_id"], "один"
            ),
        )
        self.assertEqual(sum("error" not in result for result in results), 1)
        async with self.database.connection() as conn:
            reviews = await db.fetch_one(
                conn, "SELECT count(*)::int AS count FROM reviews WHERE task_id = %s", (task["task_id"],)
            )
            progress = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (task["word_id"],)
            )
        self.assertEqual(reviews["count"], 1)
        self.assertEqual(progress["reps"], 1)

    async def test_concurrent_task_creation_leaves_one_open_task(self) -> None:
        first, second = await asyncio.gather(
            scheduler.create_task(self.database, self.owner["id"], word_query="eins"),
            scheduler.create_task(self.database, self.owner["id"], word_query="zwei"),
        )
        async with self.database.connection() as conn:
            rows = await db.fetch_all(
                conn, "SELECT id, status FROM tasks WHERE user_id = %s", (self.owner["id"],)
            )
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(row["status"] == "open" for row in rows), 1)
        self.assertEqual(sum(row["status"] == "voided" for row in rows), 1)

    async def test_two_learners_are_independent(self) -> None:
        spouse_deck = await words.create_deck(
            self.database, self.spouse["id"], "de", "Core"
        )
        await words.add_word(
            self.database,
            user_id=self.spouse["id"],
            language="de",
            deck_id=spouse_deck["id"],
            word=make_word("eins", "единица супруга"),
        )
        owner_task, spouse_task = await asyncio.gather(
            scheduler.create_task(self.database, self.owner["id"], word_query="eins"),
            scheduler.create_task(self.database, self.spouse["id"], word_query="eins"),
        )
        self.assertNotEqual(owner_task["task_id"], spouse_task["task_id"])
        self.assertIn("единица супруга", spouse_task["options"])
        self.assertNotIn("единица супруга", owner_task["options"])
        foreign = await scheduler.submit_answer(
            self.database, self.owner["id"], spouse_task["task_id"], "единица супруга"
        )
        self.assertEqual(foreign["error"], "exercise expired")

    async def test_deck_session_drains_due_then_new_and_ends(self) -> None:
        deck = await words.create_deck(self.database, self.owner["id"], "de", "Tiny")
        due, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=deck["id"],
            word=make_word("alt", "старый"),
        )
        await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=deck["id"],
            word=make_word("neu", "новый"),
        )
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 1, 1, now() - interval '1 minute')""",
                (due["id"],),
            )
        session = await scheduler.start_session(
            self.database, self.owner["id"], kind="long", deck_id=deck["id"]
        )
        first = await scheduler.create_task(
            self.database,
            self.owner["id"],
            deck_id=deck["id"],
            session_id=session["id"],
        )
        self.assertEqual(first["word"], "alt")
        await scheduler.submit_answer(
            self.database, self.owner["id"], first["task_id"], "alt"
        )
        second = await scheduler.create_task(
            self.database,
            self.owner["id"],
            deck_id=deck["id"],
            session_id=session["id"],
        )
        self.assertEqual(second["word"], "neu")
        await scheduler.submit_answer(
            self.database, self.owner["id"], second["task_id"], "новый"
        )
        empty = await scheduler.create_task(
            self.database,
            self.owner["id"],
            deck_id=deck["id"],
            session_id=session["id"],
        )
        self.assertIsNone(empty)

    async def test_push_claim_rules_and_restart_idempotency(self) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 3, 7, %s)""",
                (self.word_rows[0]["id"], now - timedelta(minutes=1)),
            )
        policy = await reminders.get_policy(self.database, self.owner["id"])
        await reminders.update_policy(
            self.database,
            self.owner["id"],
            expected_revision=policy["revision"],
            mode="smart",
            window_start="00:00",
            window_end="23:59",
            target_interval_minutes=180,
        )
        scheduled = now + timedelta(minutes=20)
        scheduled += timedelta(minutes=(-scheduled.minute) % 5)
        local = scheduled.astimezone(ZoneInfo("Europe/Berlin"))
        await reminders.materialize_plan(
            self.database,
            self.owner["id"],
            [{
                "local_date": local.date().isoformat(),
                "local_time": local.strftime("%H:%M"),
                "word_ids": [self.word_rows[0]["id"]],
                "text": "test",
            }],
            source="deterministic",
            now=now,
        )
        first, second = await asyncio.gather(
            scheduler.claim_push(self.database, self.owner["id"], now=scheduled),
            scheduler.claim_push(self.database, self.owner["id"], now=scheduled),
        )
        self.assertEqual(sum(item is not None for item in (first, second)), 1)
        restarted_attempt = await scheduler.claim_push(
            self.database, self.owner["id"], now=scheduled + timedelta(minutes=1)
        )
        self.assertIsNone(restarted_attempt)

        await scheduler.start_session(self.database, self.spouse["id"], kind="long")
        spouse_deck = await words.create_deck(self.database, self.spouse["id"], "de", "Due")
        spouse_word, _ = await words.add_word(
            self.database,
            user_id=self.spouse["id"],
            language="de",
            deck_id=spouse_deck["id"],
            word=make_word("fällig", "просроченный"),
        )
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 3, 7, now() - interval '1 minute')""",
                (spouse_word["id"],),
            )
        suppressed = await scheduler.claim_push(self.database, self.spouse["id"], now=now)
        self.assertIsNone(suppressed)

    async def test_lapse_retry_alone_does_not_trigger_push(self) -> None:
        task = await scheduler.create_task(self.database, self.owner["id"], word_query="eins")
        await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "неверно"
        )
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = now() - interval '1 minute' WHERE word_id = %s",
                (task["word_id"],),
            )
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        claim = await scheduler.claim_push(self.database, self.owner["id"], now=now)
        self.assertIsNone(claim)

    async def test_micro_session_uses_due_words_only(self) -> None:
        session = await scheduler.start_session(
            self.database, self.owner["id"], kind="micro", target_count=5
        )
        self.assertIsNone(
            await scheduler.create_task(
                self.database, self.owner["id"], session_id=session["id"]
            )
        )
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 3, 7, now() - interval '1 minute')""",
                (self.word_rows[0]["id"],),
            )
        task = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertEqual(task["word_id"], self.word_rows[0]["id"])

    async def test_quick_session_orders_by_progress_and_introduces_new_words(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(days=10)
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 5, 30, %s), (%s, 1, 1, %s), (%s, 3, 7, %s)""",
                (
                    self.word_rows[0]["id"], future,
                    self.word_rows[1]["id"], future,
                    self.word_rows[3]["id"], future,
                ),
            )
        session = await scheduler.start_session(
            self.database, self.owner["id"], kind="quick", deck_id=self.deck["id"]
        )

        first = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertEqual(first["word"], "drei")
        self.assertEqual(first["type"], "choice")
        self.assertEqual(first["prompt"], "Что означает «drei»?")
        await scheduler.submit_answer(
            self.database, self.owner["id"], first["task_id"], "три"
        )

        second = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertEqual(second["word"], "zwei")
        self.assertEqual(second["type"], "choice_ru_de")
        self.assertEqual(second["prompt"], "Как по-немецки «два»?")
        self.assertIn("zwei", second["options"])
        await scheduler.submit_answer(
            self.database, self.owner["id"], second["task_id"], "zwei"
        )

        third = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertEqual(third["word"], "vier")
        self.assertEqual(third["type"], "choice")
        async with self.database.connection() as conn:
            introduced = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (first["word_id"],)
            )
            untouched = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (second["word_id"],)
            )
            reviews = await db.fetch_all(
                conn,
                "SELECT task_id FROM reviews WHERE task_id = ANY(%s)",
                ([first["task_id"], second["task_id"]],),
            )
            saved_session = await db.fetch_one(
                conn, "SELECT * FROM sessions WHERE id = %s", (session["id"],)
            )
        self.assertEqual(introduced["reps"], 1)
        self.assertEqual(untouched["reps"], 1)
        self.assertEqual(untouched["interval_days"], 1)
        self.assertEqual(untouched["due_at"], future)
        self.assertEqual([row["task_id"] for row in reviews], [first["task_id"]])
        self.assertEqual(saved_session["answered_count"], 2)

    async def test_quick_session_applies_due_review_and_expires_after_five_minutes(self) -> None:
        deck = await words.create_deck(
            self.database, self.owner["id"], "de", "Quick"
        )
        row, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=deck["id"],
            word=make_word("heute", "сегодня"),
        )
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 1, 1, now() - interval '1 minute')""",
                (row["id"],),
            )
        session = await scheduler.start_session(
            self.database, self.owner["id"], kind="quick", deck_id=deck["id"]
        )
        due_task = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        await scheduler.submit_answer(
            self.database, self.owner["id"], due_task["task_id"], "сегодня"
        )
        async with self.database.connection() as conn:
            progressed = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (row["id"],)
            )
            review = await db.fetch_one(
                conn, "SELECT * FROM reviews WHERE task_id = %s", (due_task["task_id"],)
            )
        self.assertEqual(progressed["reps"], 2)
        self.assertIsNotNone(review)

        next_task = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE sessions SET created_at = now() - interval '6 minutes' WHERE id = %s",
                (session["id"],),
            )
        expired = await scheduler.submit_answer(
            self.database, self.owner["id"], next_task["task_id"], "сегодня"
        )
        self.assertEqual(expired["error"], "session expired")
        async with self.database.connection() as conn:
            saved_session = await db.fetch_one(
                conn, "SELECT * FROM sessions WHERE id = %s", (session["id"],)
            )
            expired_review = await db.fetch_one(
                conn, "SELECT * FROM reviews WHERE task_id = %s", (next_task["task_id"],)
            )
        self.assertEqual(saved_session["status"], "expired")
        self.assertIsNone(expired_review)


if __name__ == "__main__":
    unittest.main()
