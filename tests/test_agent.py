from __future__ import annotations

import json
import unittest
from decimal import Decimal
from types import SimpleNamespace

from pydantic import SecretStr

from bot.agent import (
    AgentConjugation,
    AgentWordCard,
    CARD_CORRECTION_INSTRUCTIONS,
    INSTRUCTIONS,
    CardCorrectionOutput,
    TOOLS,
    TutorService,
)
from bot.config import Settings
from bot.context import CONTEXT_INSTRUCTIONS, ContextExerciseOutput
from bot.grader import GRADER_INSTRUCTIONS
from vocab import db, llm, scheduler, words
from vocab.models import Word
from tests.support import temporary_database


class AgentSchemaTests(unittest.TestCase):
    def test_all_function_tools_have_strict_schemas(self) -> None:
        self.assertTrue(all(tool.strict_json_schema for tool in TOOLS))

    def test_fixed_conjugation_schema_converts_to_database_card(self) -> None:
        six = ["ich gehe", "du gehst", "er geht", "wir gehen", "ihr geht", "Sie gehen"]
        card = AgentWordCard(
            lemma="gehen",
            kind="verb",
            translation="идти",
            example="Ich gehe nach Hause.",
            pronunciation="геен",
            article=None,
            singular=None,
            plural_full=None,
            conjugation=AgentConjugation(praesens=six, perfekt=six, praeteritum=six),
            verb=None,
            preposition=None,
            case=None,
            phrase_type=None,
        ).database_card()
        self.assertEqual(set(card.conjugation or {}), {"praesens", "perfekt", "praeteritum"})

    def test_lm_rules_require_articles_for_every_german_noun(self) -> None:
        self.assertIn("Never write or propose a bare German noun", INSTRUCTIONS)
        self.assertIn("lemma must equal article + bare singular", CARD_CORRECTION_INSTRUCTIONS)
        self.assertIn("missing or wrong article is rejected", GRADER_INSTRUCTIONS)
        schema = AgentWordCard.model_json_schema()["properties"]
        self.assertIn("full dictionary form with article", schema["lemma"]["description"])
        self.assertIn("together with an explicit article", schema["example"]["description"])

    def test_lm_rules_define_complete_and_compact_german_verbs(self) -> None:
        self.assertIn("Präsens, Perfekt,\nand Präteritum", INSTRUCTIONS)
        self.assertIn('"denken an Akk", without a plus sign', INSTRUCTIONS)
        self.assertIn('"sich verstehen" and "verstehen sich"', GRADER_INSTRUCTIONS)
        self.assertIn("Akkusativ", GRADER_INSTRUCTIONS)

        card_schema = AgentWordCard.model_json_schema()["properties"]
        self.assertIn("starts with 'sich'", card_schema["verb"]["description"])
        self.assertIn("Akk or Dat", card_schema["case"]["description"])
        conjugation_schema = AgentConjugation.model_json_schema()["properties"]
        self.assertIn("Präsens", conjugation_schema["praesens"]["description"])
        self.assertIn("Perfekt", conjugation_schema["perfekt"]["description"])
        self.assertIn("Präteritum", conjugation_schema["praeteritum"]["description"])

    def test_lm_rules_define_language_independent_phrase_cards(self) -> None:
        card = AgentWordCard(
            lemma="auf jeden Fall",
            kind="phrase",
            translation="в любом случае",
            example="Ich komme auf jeden Fall.",
            pronunciation="ауф йеден фаль",
            article=None,
            singular=None,
            plural_full=None,
            conjugation=None,
            verb=None,
            preposition=None,
            case=None,
            phrase_type="fixed",
        ).database_card()
        self.assertEqual(card.kind, "phrase")
        self.assertEqual(card.phrase_type, "fixed")
        self.assertIn("For any studied language", INSTRUCTIONS)
        self.assertIn("A different synonym is accepted", GRADER_INSTRUCTIONS)
        schema = AgentWordCard.model_json_schema()["properties"]
        self.assertIn("collocation", schema["phrase_type"]["description"])

    def test_context_generator_is_fresh_structured_and_low_stakes(self) -> None:
        schema = ContextExerciseOutput.model_json_schema()["properties"]

        self.assertIn("recent_contexts", CONTEXT_INSTRUCTIONS)
        self.assertIn("never multiple choice", CONTEXT_INSTRUCTIONS)
        self.assertEqual(schema["required_elements"]["maxItems"], 0)
        self.assertIn("For free_expression", GRADER_INSTRUCTIONS)
        self.assertIn("required_elements", schema)
        self.assertIn("context_tag", schema)


class LlmPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_budget_reservation_and_history_are_per_learner_and_bounded(self) -> None:
        async with temporary_database() as database:
            owner = await words.bootstrap_user(
                database, name="owner", chat_id=101, llm_monthly_cap_usd=0.30
            )
            spouse = await words.bootstrap_user(
                database, name="spouse", chat_id=202, llm_monthly_cap_usd=1.0
            )
            usage_id = await llm.reserve(database, owner["id"], "tutor", Decimal("0.25"))
            with self.assertRaises(llm.BudgetExceeded):
                await llm.reserve(database, owner["id"], "tutor", Decimal("0.10"))
            await llm.reconcile(
                database,
                usage_id,
                input_tokens=100,
                output_tokens=20,
                actual_usd=Decimal("0.01"),
            )
            await llm.reserve(database, owner["id"], "tutor", Decimal("0.10"))
            for index in range(25):
                await llm.append_chat(database, owner["id"], "user", f"owner-{index}")
            await llm.append_chat(database, spouse["id"], "user", "spouse-secret")
            history = await llm.chat_history(database, owner["id"], limit=20)
            self.assertEqual(len(history), 20)
            self.assertEqual(history[0]["content"], "owner-5")
            self.assertNotIn("spouse-secret", {item["content"] for item in history})


class CardCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_tutor_corrects_exact_answered_card_and_audits_feedback(self) -> None:
        async with temporary_database() as database:
            learner = await words.bootstrap_user(database, name="owner", chat_id=101)
            deck = await words.create_deck(database, learner["id"], "de", "A2")
            word, _ = await words.add_word(
                database,
                user_id=learner["id"],
                language="de",
                deck_id=deck["id"],
                word=Word(
                    lemma="morgen",
                    kind="other",
                    translation="вчера",
                    example="Bis morgen.",
                    pronunciation="морген",
                    source_file="test",
                ),
            )
            task = await scheduler.create_task(
                database,
                learner["id"],
                word_id=word["id"],
                task_type="flashcard_ru_de",
            )
            assert task is not None
            await scheduler.submit_answer(
                database, learner["id"], task["task_id"], "morgen"
            )
            await words.flag_answered_task_word(
                database,
                learner["id"],
                task["task_id"],
                reason="Перевод должен быть «завтра»",
            )
            corrected = AgentWordCard(
                lemma="morgen",
                kind="other",
                translation="завтра",
                example="Bis morgen.",
                pronunciation="морген",
                article=None,
                singular=None,
                plural_full=None,
                conjugation=None,
                verb=None,
                preposition=None,
                case=None,
                phrase_type=None,
            )
            calls: list[tuple] = []

            class FakeResult:
                context_wrapper = SimpleNamespace(
                    usage=SimpleNamespace(input_tokens=100, output_tokens=20)
                )

                def final_output_as(self, output_type, *, raise_if_incorrect_type):
                    return CardCorrectionOutput(
                        corrected_card=corrected,
                        summary_ru="Исправлен перевод.",
                    )

            async def runner(*args, **kwargs):
                calls.append((args, kwargs))
                return FakeResult()

            service = TutorService(
                Settings(
                    TELEGRAM_BOT_TOKEN="token",
                    DATABASE_URL="postgresql://unused",
                    OWNER_CHAT_ID=101,
                    OPENAI_API_KEY=SecretStr("test-key"),
                    TUTOR_MODEL="tutor-test",
                ),
                runner=runner,
            )
            result = await service.correct_card(
                database,
                learner,
                task_id=task["task_id"],
                feedback="Перевод должен быть «завтра»",
            )
            self.assertEqual(result.corrected_card.translation, "завтра")
            agent, payload = calls[0][0][:2]
            self.assertEqual(agent.tools, [])
            self.assertEqual(
                json.loads(payload)["learner_feedback"],
                "Перевод должен быть «завтра»",
            )
            async with database.connection() as conn:
                stored = await db.fetch_one(
                    conn, "SELECT * FROM words WHERE id = %s", (word["id"],)
                )
                audit = await db.fetch_one(
                    conn,
                    "SELECT * FROM card_corrections WHERE word_id = %s",
                    (word["id"],),
                )
            self.assertEqual(stored["card_status"], "active")
            self.assertEqual(stored["card"]["translation"], "завтра")
            self.assertEqual(audit["feedback"], "Перевод должен быть «завтра»")
            self.assertEqual(audit["original_card"]["translation"], "вчера")
            self.assertEqual(audit["corrected_card"]["translation"], "завтра")


if __name__ == "__main__":
    unittest.main()
