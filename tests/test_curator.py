from __future__ import annotations

import unittest

from tests.support import temporary_database
from vocab import curator, db, scheduler, words
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
            composed = await scheduler.compose_push(database, owner["id"])
            self.assertEqual(composed["source"], "curator")
            async with database.connection() as conn:
                await conn.execute(
                    """INSERT INTO curator_runs(user_id, kind, status, error)
                       VALUES (%s, 'plan', 'failed', 'mock outage')""",
                    (owner["id"],),
                )
            fallback = await scheduler.compose_push(database, owner["id"])
            self.assertEqual(fallback["source"], "deterministic")
            first = await scheduler.claim_digest(
                database, owner["id"], "2026-07-20", {"digest": "weekly"}
            )
            second = await scheduler.claim_digest(
                database, owner["id"], "2026-07-20", {"digest": "weekly"}
            )
            self.assertIsNotNone(first)
            self.assertIsNone(second)


if __name__ == "__main__":
    unittest.main()
