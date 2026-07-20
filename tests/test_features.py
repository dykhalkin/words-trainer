from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import unittest

from tests.support import temporary_database
from vocab import db, jobs, scheduler, statistics, words
from vocab.models import Word


def card(lemma: str, translation: str) -> Word:
    return Word(
        lemma=lemma,
        kind="other",
        translation=translation,
        example=f"Beispiel: {lemma}.",
        pronunciation=lemma,
        source_file="test.csv",
    )


class NewFeatureTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=7001, timezone="Europe/Berlin"
        )
        self.other = await words.bootstrap_user(
            self.database, name="other", chat_id=7002
        )
        self.a2 = await words.create_deck(
            self.database, self.owner["id"], "de", "A2"
        )
        self.doctor = await words.create_deck(
            self.database, self.owner["id"], "de", "Doctor"
        )

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def add(self, lemma: str, translation: str, deck_id: int | None = None) -> dict:
        row, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=deck_id or self.a2["id"],
            word=card(lemma, translation),
        )
        return row

    async def test_archive_flag_restore_and_repair_preserve_progress(self) -> None:
        row = await self.add("salbe", "мазь")
        task = await scheduler.create_task(
            self.database,
            self.owner["id"],
            word_id=row["id"],
            task_type="flashcard_de_ru",
        )
        archived = await words.archive_task_word(
            self.database, self.owner["id"], task["task_id"]
        )
        self.assertEqual(archived["word_id"], row["id"])
        self.assertIsNone(
            await scheduler.create_task(self.database, self.owner["id"], word_id=row["id"])
        )
        async with self.database.connection() as conn:
            review_count = await db.fetch_one(
                conn, "SELECT count(*)::int AS count FROM reviews WHERE word_id = %s", (row["id"],)
            )
        self.assertEqual(review_count["count"], 0)

        await words.restore_word(
            self.database, self.owner["id"], row["id"], self.a2["id"]
        )
        task = await scheduler.create_task(
            self.database,
            self.owner["id"],
            word_id=row["id"],
            task_type="flashcard_de_ru",
        )
        await scheduler.submit_answer(
            self.database, self.owner["id"], task["task_id"], "мазь"
        )
        await words.flag_word(self.database, self.owner["id"], row["id"], reason="typo")
        issue = (await words.list_word_issues(self.database, self.owner["id"]))[0]
        self.assertEqual(issue["needs_fix_reason"], "typo")
        fixed = await words.replace_word_card(
            self.database,
            self.owner["id"],
            row["id"],
            {
                "lemma": "die Salbe",
                "kind": "noun",
                "translation": "мазь",
                "example": "Die Salbe hilft.",
                "pronunciation": "зальбэ",
                "article": "die",
                "singular": "Salbe",
                "plural_full": "die Salben",
            },
        )
        self.assertEqual(fixed["card_status"], "active")
        async with self.database.connection() as conn:
            progress = await db.fetch_one(
                conn,
                "SELECT p.*, now() AS database_now FROM progress p WHERE word_id = %s",
                (row["id"],),
            )
        self.assertEqual(progress["reps"], 1)
        self.assertLessEqual(progress["due_at"], progress["database_now"])

    async def test_answer_archive_race_has_one_winner(self) -> None:
        row = await self.add("bleiben", "оставаться")
        task = await scheduler.create_task(
            self.database,
            self.owner["id"],
            word_id=row["id"],
            task_type="flashcard_de_ru",
        )
        answer, archived = await asyncio.gather(
            scheduler.submit_answer(
                self.database, self.owner["id"], task["task_id"], "оставаться"
            ),
            words.archive_task_word(self.database, self.owner["id"], task["task_id"]),
        )
        async with self.database.connection() as conn:
            review = await db.fetch_one(
                conn, "SELECT count(*)::int AS count FROM reviews WHERE task_id = %s", (task["task_id"],)
            )
            state = await db.fetch_one(
                conn,
                "SELECT t.status, d.is_archive FROM tasks t "
                "JOIN words w ON w.id=t.word_id JOIN decks d ON d.id=w.deck_id WHERE t.id=%s",
                (task["task_id"],),
            )
        self.assertIn(state["status"], {"answered", "voided"})
        self.assertEqual(review["count"] + int(state["is_archive"]), 1)
        self.assertEqual(archived is not None, state["is_archive"])
        self.assertEqual("error" not in answer, state["status"] == "answered")

    async def test_session_scope_validation_and_archive_exclusion(self) -> None:
        first = await self.add("eins", "один", self.a2["id"])
        second = await self.add("zwei", "два", self.doctor["id"])
        session = await scheduler.start_session(
            self.database, self.owner["id"], kind="long", deck_id=self.doctor["id"]
        )
        task = await scheduler.create_task(
            self.database, self.owner["id"], session_id=session["id"]
        )
        self.assertEqual(task["word_id"], second["id"])
        archive = await words.ensure_archive_deck(self.database, self.owner["id"], "de")
        with self.assertRaises(ValueError):
            await scheduler.start_session(
                self.database, self.owner["id"], kind="long", deck_id=archive["id"]
            )
        other_deck = await words.create_deck(
            self.database, self.other["id"], "de", "Private"
        )
        with self.assertRaises(ValueError):
            await scheduler.start_session(
                self.database, self.owner["id"], kind="long", deck_id=other_deck["id"]
            )
        self.assertNotEqual(first["id"], second["id"])

    async def test_stats_use_local_days_and_review_deck_snapshot(self) -> None:
        row = await self.add("heute", "сегодня")
        review_ids = []
        for _ in range(2):
            task = await scheduler.create_task(
                self.database,
                self.owner["id"],
                word_id=row["id"],
                task_type="flashcard_de_ru",
            )
            await scheduler.submit_answer(
                self.database, self.owner["id"], task["task_id"], "сегодня"
            )
            async with self.database.connection() as conn:
                saved = await db.fetch_one(
                    conn, "SELECT id FROM reviews WHERE task_id = %s", (task["task_id"],)
                )
            review_ids.append(saved["id"])
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE reviews SET created_at = %s WHERE id = %s",
                (datetime(2026, 3, 28, 23, 30, tzinfo=timezone.utc), review_ids[0]),
            )
            await conn.execute(
                "UPDATE reviews SET created_at = %s WHERE id = %s",
                (datetime(2026, 3, 29, 22, 30, tzinfo=timezone.utc), review_ids[1]),
            )
        await words.move_word(
            self.database, self.owner["id"], row["id"], self.doctor["id"]
        )
        now = datetime(2026, 3, 29, 12, tzinfo=timezone.utc)
        old_deck = await statistics.stats(
            self.database, self.owner["id"], deck_id=self.a2["id"], days=2, now=now
        )
        new_deck = await statistics.stats(
            self.database, self.owner["id"], deck_id=self.doctor["id"], days=2, now=now
        )
        self.assertEqual(old_deck["reviews_last_7d"]["count"], 1)
        self.assertEqual(old_deck["today"], {"review_attempts": 1, "unique_words": 1})
        self.assertEqual(new_deck["reviews_last_7d"]["count"], 0)
        self.assertEqual(new_deck["words_total"], 1)

    async def test_job_controls_are_persistent_and_claim_once(self) -> None:
        await jobs.ensure_controls(self.database)
        await jobs.set_enabled(self.database, "push", False)
        with self.assertRaises(ValueError):
            await jobs.enqueue_run(self.database, "push")
        queued = await jobs.enqueue_run(self.database, "push", force=True)
        first, second = await asyncio.gather(
            jobs.claim_queued(self.database), jobs.claim_queued(self.database)
        )
        claimed = [row for row in (first, second) if row]
        self.assertEqual([row["id"] for row in claimed], [queued["id"]])
        skipped = await jobs.begin_scheduled_run(
            self.database, "push", "scheduled:push:test"
        )
        duplicate = await jobs.begin_scheduled_run(
            self.database, "push", "scheduled:push:test"
        )
        self.assertEqual(skipped["status"], "skipped")
        self.assertIsNone(duplicate)
