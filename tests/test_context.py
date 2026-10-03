from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from pydantic import SecretStr

from bot.config import Settings
from bot.context import ContextExerciseOutput, ContextExerciseService
from tests.support import temporary_database
from vocab import context, db, scheduler, words
from vocab.models import Phrase


def phrase(lemma: str, translation: str) -> Phrase:
    return Phrase(
        lemma=lemma,
        kind="phrase",
        translation=translation,
        example=f"Das ist: {lemma}.",
        pronunciation=lemma,
        source_file="test",
        phrase_type="fixed",
    )


class ContextModeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.database_context = temporary_database()
        self.database = await self.database_context.__aenter__()
        self.learner = await words.bootstrap_user(
            self.database, name="owner", chat_id=701
        )
        self.other = await words.bootstrap_user(
            self.database, name="other", chat_id=702
        )
        self.deck = await words.create_deck(
            self.database, self.learner["id"], "de", "Context"
        )
        other_deck = await words.create_deck(
            self.database, self.other["id"], "de", "Private"
        )
        self.word, _ = await words.add_word(
            self.database,
            user_id=self.learner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=phrase("auf jeden Fall", "в любом случае"),
        )
        await words.add_word(
            self.database,
            user_id=self.other["id"],
            language="de",
            deck_id=other_deck["id"],
            word=phrase("spouse-secret", "чужой секрет"),
        )

    async def asyncTearDown(self) -> None:
        await self.database_context.__aexit__(None, None, None)

    async def test_llm_tasks_are_fresh_scoped_and_do_not_change_existing_progress(self) -> None:
        due_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, lapses, ease, interval_days, due_at)
                   VALUES (%s, 3, 2, 2.1, 8, %s)""",
                (self.word["id"], due_at),
            )
            progress_before = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (self.word["id"],)
            )
        session = await scheduler.start_session(
            self.database,
            self.learner["id"],
            kind="context",
            deck_id=self.deck["id"],
        )
        calls: list[tuple] = []
        outputs = [
            ContextExerciseOutput(
                mode="reply",
                situation_ru="Друг сомневается, придёшь ли ты на встречу.",
                task="Ответь коротко по-немецки, что ты придёшь в любом случае.",
                reference_answer="Ich komme auf jeden Fall.",
                required_elements=[],
                context_tag="friends:meeting",
                explanation_ru="Фраза подчёркивает, что решение не изменится.",
            ),
            ContextExerciseOutput(
                mode="reply",
                situation_ru="Друг сомневается, придёшь ли ты на встречу.",
                task="Ответь коротко по-немецки, что ты придёшь в любом случае.",
                reference_answer="Ich komme auf jeden Fall.",
                required_elements=[],
                context_tag="friends:meeting",
                explanation_ru="Фраза подчёркивает, что решение не изменится.",
            ),
            ContextExerciseOutput(
                mode="reply",
                situation_ru="Коллега спрашивает, закончишь ли ты отчёт сегодня.",
                task="Ответь по-немецки, что обязательно закончишь его.",
                reference_answer="Ich beende ihn auf jeden Fall heute.",
                required_elements=[],
                context_tag="work:report",
                explanation_ru="Фраза выражает твёрдую уверенность.",
            ),
        ]

        class FakeResult:
            context_wrapper = SimpleNamespace(
                usage=SimpleNamespace(input_tokens=100, output_tokens=30)
            )

            def __init__(self, output: ContextExerciseOutput) -> None:
                self.output = output

            def final_output_as(self, output_type, *, raise_if_incorrect_type):
                return self.output

        async def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return FakeResult(outputs[len(calls) - 1])

        service = ContextExerciseService(
            Settings(
                TELEGRAM_BOT_TOKEN="token",
                DATABASE_URL="postgresql://unused",
                OWNER_CHAT_ID=self.learner["chat_id"],
                OPENAI_API_KEY=SecretStr("test-key"),
                TUTOR_MODEL="context-test",
            ),
            runner=runner,
        )
        first = await service.create_task(
            self.database, self.learner, session["id"]
        )
        self.assertEqual(first["type"], "context_llm")
        self.assertIn("Друг сомневается", first["prompt"])
        open_context = await scheduler.task_context(
            self.database, self.learner["id"], first["task_id"]
        )
        self.assertNotIn("expected", open_context)

        verdict = await scheduler.submit_answer(
            self.database,
            self.learner["id"],
            first["task_id"],
            "Ich komme auf jeden Fall.",
        )
        self.assertTrue(verdict["correct"])
        self.assertTrue(verdict["practice_only"])
        self.assertIn("решение не изменится", verdict["context_explanation"])

        second = await service.create_task(
            self.database, self.learner, session["id"]
        )
        self.assertIn("Коллега спрашивает", second["prompt"])
        self.assertEqual(len(calls), 3)
        first_api_input = json.loads(calls[0][0][1])
        second_api_input = json.loads(calls[1][0][1])
        retry_api_input = json.loads(calls[2][0][1])
        self.assertNotIn("word_id", first_api_input)
        self.assertNotIn("session_id", first_api_input)
        self.assertNotIn("spouse-secret", json.dumps(first_api_input))
        self.assertEqual(second_api_input["recent_contexts"][0]["context_tag"], "friends:meeting")
        self.assertEqual(len(retry_api_input["recent_contexts"]), 2)
        self.assertEqual(calls[0][0][0].tools, [])
        self.assertEqual(calls[0][1]["max_turns"], 1)

        async with self.database.connection() as conn:
            progress_after = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (self.word["id"],)
            )
            reviews = await db.fetch_one(
                conn,
                "SELECT count(*)::int AS count FROM reviews WHERE word_id = %s",
                (self.word["id"],),
            )
            saved_session = await db.fetch_one(
                conn, "SELECT * FROM sessions WHERE id = %s", (session["id"],)
            )
            usage = await db.fetch_all(
                conn,
                "SELECT kind, status FROM llm_usage WHERE user_id = %s ORDER BY id",
                (self.learner["id"],),
            )
        self.assertEqual(progress_after, progress_before)
        self.assertEqual(reviews["count"], 0)
        self.assertEqual(saved_session["status"], "open")
        self.assertEqual(saved_session["target_count"], None)
        self.assertEqual(saved_session["answered_count"], 1)
        self.assertEqual(
            usage,
            [
                {"kind": "context_generator", "status": "reconciled"},
                {"kind": "context_generator", "status": "reconciled"},
                {"kind": "context_generator", "status": "reconciled"},
            ],
        )

    async def test_context_answer_does_not_create_progress_for_a_new_card(self) -> None:
        session = await scheduler.start_session(
            self.database,
            self.learner["id"],
            kind="context",
            deck_id=self.deck["id"],
        )
        candidate_data = await context.candidate(
            self.database, self.learner["id"], session["id"]
        )
        task = await context.save_task(
            self.database,
            self.learner["id"],
            candidate_data,
            {
                "mode": "reply",
                "situation_ru": "Тебя спрашивают, уверен ли ты.",
                "task": "Ответь по-немецки: «в любом случае».",
                "reference_answer": "Auf jeden Fall.",
                "required_elements": ["auf jeden Fall"],
                "context_tag": "decision:certainty",
                "explanation_ru": "Это устойчивый ответ с нужным значением.",
            },
        )
        verdict = await scheduler.submit_answer(
            self.database,
            self.learner["id"],
            task["task_id"],
            "Auf jeden Fall.",
        )
        async with self.database.connection() as conn:
            progress = await db.fetch_one(
                conn, "SELECT * FROM progress WHERE word_id = %s", (self.word["id"],)
            )
            reviews = await db.fetch_one(
                conn,
                "SELECT count(*)::int AS count FROM reviews WHERE word_id = %s",
                (self.word["id"],),
            )
        self.assertTrue(verdict["correct"])
        self.assertIsNone(progress)
        self.assertEqual(reviews["count"], 0)


if __name__ == "__main__":
    unittest.main()
