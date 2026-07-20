from __future__ import annotations

import unittest

from psycopg import errors
from psycopg.types.json import Jsonb

from tests.support import temporary_database
from vocab.db import Database, DatabaseUnavailable


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
                ],
            )

    async def test_database_unavailable_is_clear(self) -> None:
        database = Database(
            "postgresql://none:none@127.0.0.1:59999/none", open_timeout=0.15
        )
        with self.assertRaisesRegex(DatabaseUnavailable, "PostgreSQL is unavailable"):
            await database.open()


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

    async def test_general_deck_is_unique_per_user_and_language(self) -> None:
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO decks(user_id, language, name, normalized_name, is_general)
                   VALUES (%s, 'de', 'General', 'general', true)""",
                (self.owner_id,),
            )
            with self.assertRaises(errors.UniqueViolation):
                async with conn.transaction():
                    await conn.execute(
                        """INSERT INTO decks(user_id, language, name, normalized_name, is_general)
                           VALUES (%s, 'de', 'Other general', 'other general', true)""",
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


if __name__ == "__main__":
    unittest.main()
