from __future__ import annotations

import random
import unittest

from pydantic import ValidationError

from tests.support import temporary_database
from vocab import db, words
from vocab.exercises import choice, grammar
from vocab.languages import ExerciseContext, language_spec
from vocab.models import PERSONS, TENSES, Verb, Word


def simple_word(lemma: str, translation: str) -> Word:
    return Word(
        lemma=lemma,
        kind="other",
        translation=translation,
        example=f"Beispiel mit {lemma}.",
        pronunciation=lemma,
        source_file="test.csv",
    )


class WordLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=1001
        )
        self.spouse = await words.bootstrap_user(
            self.database, name="spouse", chat_id=1002
        )
        self.deck = await words.create_deck(self.database, self.owner["id"], "de", "A2")

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def test_agent_card_validation_and_persistent_staging(self) -> None:
        incomplete = {
            "lemma": "gehen",
            "kind": "verb",
            "translation": "идти",
            "example": "Ich gehe.",
            "pronunciation": "ge-en",
            "conjugation": {"praesens": ["ich gehe"]},
        }
        with self.assertRaisesRegex(Exception, "Präsens|tenses|six"):
            words.validate_card("de", incomplete)

        card = {
            "lemma": "die Praxis",
            "kind": "noun",
            "translation": "врачебный кабинет",
            "example": "Die Praxis ist heute geöffnet.",
            "pronunciation": "prak-sis",
            "article": "die",
            "singular": "Praxis",
            "plural_full": "die Praxen",
        }
        staged = await words.stage_cards(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_name="Arztbesuch",
            cards=[card],
        )
        committed = await words.commit_pending(
            self.database, self.owner["id"], staged["pending_id"]
        )
        self.assertEqual(committed["inserted"], 1)
        stored = await words.get_word(self.database, self.owner["id"], "Praxis")
        self.assertEqual(stored.translation, "врачебный кабинет")

    async def test_language_normalization_preserves_meaningful_german_spelling(self) -> None:
        first, collision = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=simple_word("Maße", "размеры"),
        )
        duplicate, collision2 = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=simple_word("  MAẞE ", "размеры"),
        )
        distinct, collision3 = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=simple_word("Masse", "масса"),
        )
        self.assertFalse(collision)
        self.assertTrue(collision2)
        self.assertEqual(first["id"], duplicate["id"])
        self.assertFalse(collision3)
        self.assertNotEqual(first["id"], distinct["id"])

    async def test_delete_deck_moves_words_and_protects_general(self) -> None:
        inserted, _ = await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="de",
            deck_id=self.deck["id"],
            word=simple_word("bleiben", "оставаться"),
        )
        result = await words.delete_deck(self.database, self.owner["id"], self.deck["id"])
        self.assertEqual(result["moved_words"], 1)
        stored = await words.get_word(self.database, self.owner["id"], inserted["id"])
        self.assertEqual(stored.deck_id, result["general_deck_id"])
        with self.assertRaisesRegex(ValueError, "general deck"):
            await words.delete_deck(
                self.database, self.owner["id"], result["general_deck_id"]
            )

    async def test_generator_candidates_are_user_and_language_scoped(self) -> None:
        translations = ["один", "два", "три", "четыре"]
        owner_words = []
        for index, translation in enumerate(translations):
            row, _ = await words.add_word(
                self.database,
                user_id=self.owner["id"],
                language="de",
                deck_id=self.deck["id"],
                word=simple_word(f"wort{index}", translation),
            )
            owner_words.append(row)

        spouse_deck = await words.create_deck(
            self.database, self.spouse["id"], "de", "Private"
        )
        await words.add_word(
            self.database,
            user_id=self.spouse["id"],
            language="de",
            deck_id=spouse_deck["id"],
            word=simple_word("geheim", "СЕКРЕТ-СУПРУГИ"),
        )
        french = await words.create_deck(self.database, self.owner["id"], "fr", "French")
        await words.add_word(
            self.database,
            user_id=self.owner["id"],
            language="fr",
            deck_id=french["id"],
            word=simple_word("secret", "СЕКРЕТ-ФРАНЦУЗСКИЙ"),
        )

        async with self.database.connection() as conn:
            row = await db.fetch_one(conn, "SELECT * FROM words WHERE id = %s", (owner_words[0]["id"],))
            target = db.word_from_row(row)
            payload, _ = await choice.generate(
                target,
                conn,
                random.Random(1),
                ExerciseContext(self.owner["id"], "de"),
            )
        self.assertEqual(set(payload["options"]), set(translations))
        self.assertNotIn("СЕКРЕТ-СУПРУГИ", payload["options"])
        self.assertNotIn("СЕКРЕТ-ФРАНЦУЗСКИЙ", payload["options"])
        self.assertNotIn("grammar", language_spec("fr").exercise_types)

    async def test_partial_verb_generator_returns_none(self) -> None:
        partial = Verb(
            lemma="gehen",
            kind="verb",
            translation="идти",
            example="Ich gehe.",
            pronunciation="ge-en",
            conjugation={"praesens": ["ich gehe"]},
        )
        async with self.database.connection() as conn:
            generated = await grammar.generate(
                partial,
                conn,
                random.Random(1),
                ExerciseContext(self.owner["id"], "de"),
            )
        self.assertIsNone(generated)


if __name__ == "__main__":
    unittest.main()
