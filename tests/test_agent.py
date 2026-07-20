from __future__ import annotations

import unittest
from decimal import Decimal

from bot.agent import AgentConjugation, AgentWordCard, TOOLS
from vocab import llm, words
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
        ).database_card()
        self.assertEqual(set(card.conjugation or {}), {"praesens", "perfekt", "praeteritum"})


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


if __name__ == "__main__":
    unittest.main()
