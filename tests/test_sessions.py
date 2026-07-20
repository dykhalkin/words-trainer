from __future__ import annotations

import asyncio
import random
import unittest
from datetime import datetime, timedelta, timezone

from tests.support import temporary_database
from vocab import db, scheduler, words
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
        self.assertEqual(reused["error"], "exercise expired")
        self.assertIn("expected", answered_context)
        self.assertEqual(answered_context["answer"], "один")

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
            self.database, self.owner["id"], first["task_id"], "старый"
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
        async with self.database.connection() as conn:
            await conn.execute(
                """UPDATE users SET quiet_start = '22:00', quiet_end = '09:00',
                       min_push_interval_minutes = 180 WHERE id = %s""",
                (self.owner["id"],),
            )
            await conn.execute(
                """INSERT INTO progress(word_id, reps, interval_days, due_at)
                   VALUES (%s, 3, 7, now() - interval '1 minute')""",
                (self.word_rows[0]["id"],),
            )
        now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        first, second = await asyncio.gather(
            scheduler.claim_push(self.database, self.owner["id"], now=now),
            scheduler.claim_push(self.database, self.owner["id"], now=now),
        )
        self.assertEqual(sum(item is not None for item in (first, second)), 1)
        restarted_attempt = await scheduler.claim_push(
            self.database, self.owner["id"], now=now + timedelta(minutes=1)
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


if __name__ == "__main__":
    unittest.main()
