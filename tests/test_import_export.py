from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from psycopg.types.json import Jsonb

from tests.support import temporary_database
from vocab import db, storage, words


def write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle, delimiter=";").writerows(rows)


class ImportExportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()
        self.owner = await words.bootstrap_user(
            self.database, name="owner", chat_id=1001
        )
        self.tmp = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.root = Path(self.tmp.name)

    async def asyncTearDown(self) -> None:
        self.tmp.cleanup()
        await self.context.__aexit__(None, None, None)

    async def test_reimport_is_idempotent_and_db_edit_wins(self) -> None:
        path = self.root / "basic.csv"
        write_csv(path, [["gehen", "идти", "Ich gehe.", "ge-en"]])
        first = await storage.import_csv(
            self.database,
            path,
            user_id=self.owner["id"],
            deck_name="Basic",
            language="de",
        )
        word = await words.get_word(self.database, self.owner["id"], "gehen")
        async with self.database.connection() as conn:
            await conn.execute(
                """INSERT INTO progress(word_id, reps, due_at)
                   VALUES (%s, 2, now() + interval '1 day')""",
                (word.db_id,),
            )
        second = await storage.import_csv(
            self.database,
            path,
            user_id=self.owner["id"],
            deck_name="Basic",
            language="de",
        )
        self.assertEqual(first["added"], 1)
        self.assertEqual(second["unchanged"], 1)

        write_csv(path, [["gehen", "ходить", "Ich gehe nach Hause.", "ge-en"]])
        updated = await storage.import_csv(
            self.database,
            path,
            user_id=self.owner["id"],
            deck_name="Basic",
            language="de",
        )
        self.assertEqual(updated["updated"], 1)
        async with self.database.connection() as conn:
            row = await db.fetch_one(conn, "SELECT card FROM words WHERE id = %s", (word.db_id,))
            card = dict(row["card"])
            card["translation"] = "идти пешком (ручная правка)"
            await conn.execute(
                "UPDATE words SET card = %s, modified_at = now() WHERE id = %s",
                (Jsonb(card), word.db_id),
            )
        conflict = await storage.import_csv(
            self.database,
            path,
            user_id=self.owner["id"],
            deck_name="Basic",
            language="de",
        )
        self.assertEqual(conflict["conflict_count"], 1)
        async with self.database.connection() as conn:
            progress = await db.fetch_one(conn, "SELECT reps FROM progress WHERE word_id = %s", (word.db_id,))
        self.assertEqual(progress["reps"], 2)

    async def test_cross_deck_collision_does_not_move_word(self) -> None:
        path = self.root / "one.csv"
        write_csv(path, [["gehen", "идти", "Ich gehe.", "ge-en"]])
        first = await storage.import_csv(
            self.database, path, user_id=self.owner["id"], deck_name="One", language="de"
        )
        second = await storage.import_csv(
            self.database, path, user_id=self.owner["id"], deck_name="Two", language="de"
        )
        self.assertEqual(second["conflict_count"], 1)
        word = await words.get_word(self.database, self.owner["id"], "gehen")
        self.assertEqual(word.deck_id, first["deck_id"])

    async def test_round_trip_all_card_kinds(self) -> None:
        path = self.root / "all.csv"
        forms = [f"form {i}" for i in range(18)]
        rows = [
            ["die Salbe (die Salben)", "мазь", "Ich nutze die Salbe.", "zal-beh"],
            ["halten", "держать", "Er hält die Tür.", "hal-ten", *forms],
            ["denken an + Akk", "думать о", "Ich denke an dich.", "den-ken"],
            ["Fieber haben", "иметь температуру", "Ich habe Fieber.", "fee-ber"],
        ]
        write_csv(path, rows)
        imported = await storage.import_csv(
            self.database, path, user_id=self.owner["id"], deck_name="All", language="de"
        )
        exported_path = self.root / "export.csv"
        exported = await storage.export_deck(
            self.database,
            exported_path,
            user_id=self.owner["id"],
            deck_id=imported["deck_id"],
        )
        parsed = storage.load_file(exported_path)
        self.assertEqual(exported["rows"], 4)
        self.assertEqual([word.kind for word in parsed], ["noun", "verb", "verb_prep", "other"])
        self.assertEqual([word.lemma for word in parsed], [
            "die Salbe", "halten", "denken an + Akk", "Fieber haben"
        ])

    async def test_real_csv_counts(self) -> None:
        expected = {
            "deutsch A2 words Swetlana.csv": (55, 54, 1),
            "verbs mit prapositionen.csv": (41, 41, 0),
            "verbs v2.csv": (86, 83, 3),
        }
        for filename, (rows, unique_words, duplicate_updates) in expected.items():
            result = await storage.import_csv(
                self.database,
                Path("data") / filename,
                user_id=self.owner["id"],
                deck_name=Path(filename).stem,
                language="de",
            )
            self.assertEqual(result["rows"], rows)
            self.assertEqual(result["added"], unique_words)
            self.assertEqual(result["updated"], duplicate_updates)


if __name__ == "__main__":
    unittest.main()
