from __future__ import annotations

import unittest

from tests.support import temporary_database
from tests.test_sessions import make_word
from vocab import scheduler, words


class PracticeRotationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.user = await words.bootstrap_user(self.database, name="owner", chat_id=1001)
        self.deck = await words.create_deck(self.database, self.user["id"], "de", "Core")

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def add_cards(self, count: int, *, due: bool = False, deck=None) -> list[int]:
        ids = []
        deck_id = (deck or self.deck)["id"]
        for index in range(count):
            row, _ = await words.add_word(
                self.database, user_id=self.user["id"], language="de",
                deck_id=deck_id,
                word=make_word(
                    f"{'due' if due else 'new'}-{deck_id}-{index}", f"перевод-{index}"
                ),
            )
            ids.append(row["id"])
        if due:
            async with self.database.connection() as conn:
                await conn.execute(
                    """INSERT INTO progress(word_id, reps, interval_days, due_at)
                       SELECT unnest(%s::bigint[]), 1, 1, now() - interval '1 day'""",
                    (ids,),
                )
        return ids

    async def restart(self, *, deck=None):
        session = await scheduler.start_session(
            self.database, self.user["id"], kind="long",
            deck_id=(deck or self.deck)["id"],
        )
        return await scheduler.create_task(
            self.database, self.user["id"], session_id=session["id"]
        )

    async def test_due_cards_rotate_across_abandoned_sessions_and_wrap(self) -> None:
        ids = await self.add_cards(12, due=True)
        shown = [(await self.restart())["word_id"] for _ in ids]
        self.assertEqual(set(shown), set(ids))
        self.assertEqual((await self.restart())["word_id"], shown[0])

    async def test_new_cards_reach_beyond_first_five_without_answers(self) -> None:
        ids = await self.add_cards(12)
        shown = [(await self.restart())["word_id"] for _ in ids]
        self.assertEqual(set(shown), set(ids))
        self.assertEqual((await self.restart())["word_id"], shown[0])

    async def test_new_slots_survive_restarts_with_a_due_backlog(self) -> None:
        due = await self.add_cards(6, due=True)
        new = await self.add_cards(4)
        shown = [(await self.restart())["word_id"] for _ in range(8)]
        self.assertEqual([i + 1 for i, word_id in enumerate(shown) if word_id in new], [4, 8])
        self.assertEqual(set(shown) & set(due), set(due))
        self.assertEqual(len(set(shown)), 8)

    async def test_single_session_mixes_new_words_and_respects_neutral_skips(self) -> None:
        due = await self.add_cards(6, due=True)
        new = await self.add_cards(2)
        session = await scheduler.start_session(
            self.database, self.user["id"], kind="long", deck_id=self.deck["id"]
        )
        shown = []
        for _ in range(8):
            task = await scheduler.create_task(
                self.database, self.user["id"], session_id=session["id"]
            )
            shown.append(task["word_id"])
            await scheduler.skip_task(self.database, self.user["id"], task["task_id"])
        self.assertEqual(set(shown), set(due + new))
        self.assertIn(shown[3], new)
        self.assertIn(shown[7], new)
        self.assertIsNone(await scheduler.create_task(
            self.database, self.user["id"], session_id=session["id"]
        ))

    async def test_other_deck_history_does_not_consume_new_slots(self) -> None:
        other = await words.create_deck(self.database, self.user["id"], "de", "Other")
        await self.add_cards(3, deck=other)
        for _ in range(3):
            await self.restart(deck=other)
        due = await self.add_cards(3, due=True)
        new = await self.add_cards(1)
        shown = [(await self.restart())["word_id"] for _ in range(4)]
        self.assertEqual(set(shown[:3]), set(due))
        self.assertEqual(shown[3], new[0])

    async def test_ineligible_cards_stay_out_of_rotation(self) -> None:
        ids = await self.add_cards(3, due=True)
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = now() + interval '1 day' WHERE word_id = %s",
                (ids[0],),
            )
            await conn.execute("UPDATE words SET card_status = 'needs_fix' WHERE id = %s", (ids[1],))
        for _ in range(5):
            self.assertEqual((await self.restart())["word_id"], ids[2])
        async with self.database.connection() as conn:
            await conn.execute("UPDATE decks SET is_archive = true WHERE id = %s", (self.deck["id"],))
        self.assertIsNone(await scheduler.create_task(self.database, self.user["id"]))

    async def test_due_order_is_retained_when_exposure_is_equal(self) -> None:
        ids = await self.add_cards(3, due=True)
        async with self.database.connection() as conn:
            await conn.execute(
                "UPDATE progress SET due_at = now() - interval '10 days' WHERE word_id = %s",
                (ids[-1],),
            )
        self.assertEqual((await self.restart())["word_id"], ids[-1])
        # A due-list inspection remains chronological after a presentation.
        listed = await scheduler.list_due(self.database, self.user["id"])
        self.assertEqual(listed[0]["id"], ids[-1])
