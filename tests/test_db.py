from __future__ import annotations

import unittest
from pathlib import Path

from psycopg import errors
from psycopg.types.json import Jsonb

from tests.support import temporary_database
from vocab.db import MIGRATIONS_DIR, Database, DatabaseUnavailable


class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migrations_are_idempotent(self) -> None:
        async with temporary_database() as database:
            await database.migrate()
            async with database.connection() as conn:
                result = await conn.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
                rows = await result.fetchall()
            self.assertEqual(
                rows,
                [
                    {"version": "001_initial.sql"},
                    {"version": "002_session_ownership.sql"},
                    {"version": "003_agent_history.sql"},
                    {"version": "004_word_disposition_and_review_deck.sql"},
                    {"version": "005_job_control.sql"},
                    {"version": "006_target_language_grading_and_reminders.sql"},
                    {"version": "007_canonical_language_codes.sql"},
                    {"version": "008_complete_grading_reminder_schema.sql"},
                    {"version": "009_card_feedback_corrections.sql"},
                    {"version": "010_existing_decks_only.sql"},
                    {"version": "011_require_card_example.sql"},
                    {"version": "012_quick_sessions.sql"},
                    {"version": "013_phrase_cards.sql"},
                    {"version": "014_context_sessions.sql"},
                    {"version": "015_fair_synonym_practice.sql"},
                    {"version": "016_compact_synonym_practice.sql"},
                ],
            )

    async def test_database_unavailable_is_clear(self) -> None:
        database = Database(
            "postgresql://none:none@127.0.0.1:59999/none", open_timeout=0.15
        )
        with self.assertRaisesRegex(DatabaseUnavailable, "PostgreSQL is unavailable"):
            await database.open()

    async def test_existing_decks_migration_consolidates_without_data_loss(self) -> None:
        async with temporary_database(migrate=False) as database:
            async with database.connection() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """CREATE TABLE schema_migrations (
                               version TEXT PRIMARY KEY,
                               applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                           )"""
                    )
                    for path in sorted(Path(MIGRATIONS_DIR).glob("[0-9][0-9][0-9]_*.sql")):
                        if path.name == "010_existing_decks_only.sql":
                            break
                        await conn.execute(path.read_text(encoding="utf-8"), prepare=False)
                        await conn.execute(
                            "INSERT INTO schema_migrations(version) VALUES (%s)",
                            (path.name,),
                        )
                    user_result = await conn.execute(
                        "INSERT INTO users(name, chat_id) VALUES ('owner', 1001) RETURNING id"
                    )
                    user_id = (await user_result.fetchone())["id"]
                    target_result = await conn.execute(
                        """INSERT INTO decks(user_id, language, name, normalized_name)
                           VALUES (%s, 'de', 'B1.1', 'b1.1') RETURNING id""",
                        (user_id,),
                    )
                    target_id = (await target_result.fetchone())["id"]
                    await conn.execute(
                        """INSERT INTO decks(user_id, language, name, normalized_name, is_general)
                           VALUES (%s, 'de', 'General', 'general', true)""",
                        (user_id,),
                    )
                    extra_names = [
                        ("German", "german"),
                        ("German vocabulary", "german vocabulary"),
                        ("Общий", "общий"),
                        ("Основной", "основной"),
                        ("Общий немецкий", "общий немецкий"),
                    ]
                    word_ids = []
                    for index, (name, normalized) in enumerate(extra_names):
                        deck_result = await conn.execute(
                            """INSERT INTO decks(user_id, language, name, normalized_name)
                               VALUES (%s, 'de', %s, %s) RETURNING id""",
                            (user_id, name, normalized),
                        )
                        deck_id = (await deck_result.fetchone())["id"]
                        card = {
                            "lemma": f"word-{index}",
                            "kind": "other",
                            "translation": f"translation-{index}",
                            "example": f"Example {index}.",
                            "pronunciation": f"word-{index}",
                        }
                        word_result = await conn.execute(
                            """INSERT INTO words(
                                   user_id, language, deck_id, lemma, lemma_key, kind, card
                               ) VALUES (%s, 'de', %s, %s, %s, 'other', %s) RETURNING id""",
                            (user_id, deck_id, f"word-{index}", f"word-{index}", Jsonb(card)),
                        )
                        word_id = (await word_result.fetchone())["id"]
                        word_ids.append(word_id)
                        await conn.execute(
                            "INSERT INTO progress(word_id, reps) VALUES (%s, %s)",
                            (word_id, index + 1),
                        )
                        await conn.execute(
                            """INSERT INTO pending_cards(
                                   id, user_id, language, deck_name, cards, status, resolved_at
                               ) VALUES (%s, %s, 'de', %s, '[]', 'committed', now())""",
                            (f"pending-{index}", user_id, name),
                        )

            await database.migrate()
            await database.migrate()
            async with database.connection() as conn:
                decks = await conn.execute(
                    "SELECT id, name FROM decks WHERE user_id = %s ORDER BY id", (user_id,)
                )
                saved_words = await conn.execute(
                    """SELECT w.id, w.deck_id, p.reps FROM words w
                       JOIN progress p ON p.word_id = w.id
                       WHERE w.user_id = %s ORDER BY w.id""",
                    (user_id,),
                )
                pending = await conn.execute(
                    "SELECT DISTINCT deck_id FROM pending_cards WHERE user_id = %s", (user_id,)
                )
                columns = await conn.execute(
                    """SELECT table_name, column_name FROM information_schema.columns
                       WHERE table_schema = 'public'
                         AND ((table_name = 'decks' AND column_name = 'is_general')
                           OR (table_name = 'pending_cards' AND column_name = 'deck_name'))"""
                )
                deck_rows = await decks.fetchall()
                word_rows = await saved_words.fetchall()
                pending_rows = await pending.fetchall()
                obsolete_columns = await columns.fetchall()
            self.assertEqual(deck_rows, [{"id": target_id, "name": "B1.1"}])
            self.assertEqual([row["id"] for row in word_rows], word_ids)
            self.assertEqual([row["reps"] for row in word_rows], [1, 2, 3, 4, 5])
            self.assertEqual({row["deck_id"] for row in word_rows}, {target_id})
            self.assertEqual(pending_rows, [{"deck_id": target_id}])
            self.assertEqual(obsolete_columns, [])


class OwnershipConstraintTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        async with self.database.connection() as conn:
            owner = await conn.execute(
                "INSERT INTO users(name, chat_id) VALUES ('owner', 1001) RETURNING id"
            )
            spouse = await conn.execute(
                "INSERT INTO users(name, chat_id) VALUES ('spouse', 1002) RETURNING id"
            )
            self.owner_id = (await owner.fetchone())["id"]
            self.spouse_id = (await spouse.fetchone())["id"]
            deck = await conn.execute(
                """INSERT INTO decks(user_id, language, name, normalized_name)
                   VALUES (%s, 'de', 'A2', 'a2') RETURNING id""",
                (self.owner_id,),
            )
            self.owner_deck_id = (await deck.fetchone())["id"]

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def test_general_has_no_system_role(self) -> None:
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO decks(user_id, language, name, normalized_name)
                   VALUES (%s, 'de', 'General', 'general')""",
                (self.owner_id,),
            )
            with self.assertRaises(errors.UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO decks(user_id, language, name, normalized_name)
                           VALUES (%s, 'de', 'GENERAL', 'general')""",
                        (self.owner_id,),
                    )

    async def test_lemma_key_scope_and_composite_ownership(self) -> None:
        card = {
            "lemma": "gehen",
            "kind": "other",
            "translation": "идти",
            "example": "Ich gehe.",
            "pronunciation": "",
            "source_file": "test.csv",
        }
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO words(user_id, language, deck_id, lemma, lemma_key, kind, card)
                   VALUES (%s, 'de', %s, 'gehen', 'gehen', 'other', %s)""",
                (self.owner_id, self.owner_deck_id, Jsonb(card)),
            )
            with self.assertRaises(errors.UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO words(user_id, language, deck_id, lemma, lemma_key, kind, card)
                           VALUES (%s, 'de', %s, 'GEHEN', 'gehen', 'other', %s)""",
                        (self.owner_id, self.owner_deck_id, Jsonb(card)),
                    )

            spouse_deck = await conn.execute(
                """INSERT INTO decks(user_id, language, name, normalized_name)
                   VALUES (%s, 'de', 'A2', 'a2') RETURNING id""",
                (self.spouse_id,),
            )
            spouse_deck_id = (await spouse_deck.fetchone())["id"]
            await conn.execute(
                """INSERT INTO words(user_id, language, deck_id, lemma, lemma_key, kind, card)
                   VALUES (%s, 'de', %s, 'gehen', 'gehen', 'other', %s)""",
                (self.spouse_id, spouse_deck_id, Jsonb(card)),
            )
            with self.assertRaises(errors.ForeignKeyViolation):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO words(user_id, language, deck_id, lemma, lemma_key, kind, card)
                           VALUES (%s, 'de', %s, 'falsch', 'falsch', 'other', %s)""",
                        (self.spouse_id, self.owner_deck_id, Jsonb(card)),
                    )

    async def test_database_rejects_a_card_without_example(self) -> None:
        card = {
            "lemma": "leer",
            "kind": "other",
            "translation": "пустой",
            "pronunciation": "леер",
        }
        async with self.database.connection() as conn:
            with self.assertRaises(errors.CheckViolation):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO words(
                               user_id, language, deck_id, lemma, lemma_key, kind, card
                           ) VALUES (%s, 'de', %s, 'leer', 'leer', 'other', %s)""",
                        (self.owner_id, self.owner_deck_id, Jsonb(card)),
                    )


if __name__ == "__main__":
    unittest.main()
