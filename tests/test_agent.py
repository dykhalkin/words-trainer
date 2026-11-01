from __future__ import annotations

import unittest
from decimal import Decimal


from bot.agent import (
    AgentConjugation,
    AgentWordCard,
    INSTRUCTIONS,
    TOOLS,
)
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


class CardReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_stays_excluded_until_external_replacement(self) -> None:
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
            self.assertIsNone(await scheduler.create_task(
                database, learner["id"], word_id=word["id"], task_type="flashcard_ru_de"
            ))
            self.assertIsNone(await words.save_card_feedback(
                database, learner["id"] + 1, task["task_id"], reason="Чужая пометка"
            ))
            await words.save_card_feedback(
                database, learner["id"], task["task_id"], reason="Неверный перевод"
            )
            issues = await words.list_word_issues(database, learner["id"])
            self.assertEqual(issues[0]["needs_fix_reason"], "Неверный перевод")
            self.assertEqual(issues[0]["needs_fix_task_id"], task["task_id"])
            await words.replace_word_card(database, learner["id"], word["id"], {
                "lemma": "morgen", "kind": "other", "translation": "завтра",
                "example": "Bis morgen.", "pronunciation": "морген",
            })
            self.assertEqual(await words.list_word_issues(database, learner["id"]), [])
            self.assertIsNone(await words.save_card_feedback(
                database, learner["id"], task["task_id"], reason="Запоздалая пометка"
            ))
            self.assertIsNotNone(await scheduler.create_task(
                database, learner["id"], word_id=word["id"], task_type="flashcard_ru_de"
            ))


if __name__ == "__main__":
    unittest.main()
