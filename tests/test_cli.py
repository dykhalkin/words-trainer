from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cli
from tests.support import temporary_database


class CliManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.context = temporary_database()
        self.database = await self.context.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.context.__aexit__(None, None, None)

    async def invoke(self, *arguments: str, succeeds: bool = True) -> dict:
        args = cli.build_parser().parse_args(
            ["--database-url", self.database.dsn, "--user", "owner", *arguments]
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = await cli.run(args)
        payload = json.loads(output.getvalue())
        if succeeds:
            self.assertEqual(status, 0, payload)
        return payload

    async def test_public_commands_emit_json_and_preserve_bootstrap_settings(self) -> None:
        await self.invoke("migrate")
        bootstrapped = await self.invoke(
            "user", "bootstrap", "--owner-chat-id", "101", "--timezone", "Europe/Berlin"
        )
        self.assertEqual(bootstrapped["users"][0]["chat_id"], 101)
        await self.invoke("user", "settings", "--daily-new-limit", "7")
        await self.invoke("user", "bootstrap", "--owner-chat-id", "101", "--timezone", "UTC")
        listed = await self.invoke("user", "list")
        self.assertEqual(listed[0]["daily_new_limit"], 7)
        self.assertEqual(listed[0]["timezone"], "Europe/Berlin")

        synced = await self.invoke("sync")
        self.assertEqual(sum(item["added"] for item in synced["imports"]), 178)
        decks = await self.invoke("deck", "list")
        source_deck = next(row for row in decks["decks"] if row["word_count"])

        created = await self.invoke("deck", "create", "CLI temporary")
        renamed = await self.invoke("deck", "rename", str(created["id"]), "CLI renamed")
        self.assertEqual(renamed["name"], "CLI renamed")

        async with self.database.connection() as conn:
            row = await conn.execute(
                "SELECT id, card FROM words WHERE user_id = %s ORDER BY id LIMIT 1",
                (bootstrapped["users"][0]["id"],),
            )
            selected = await row.fetchone()
        found = await self.invoke("word", str(selected["id"]))
        self.assertEqual(found["id"], selected["id"])
        await self.invoke("history", "--limit", "5")
        await self.invoke("history", "--word", found["lemma"], "--limit", "5")
        stats = await self.invoke("stats", "--deck", str(source_deck["id"]))
        self.assertEqual(stats["deck"]["id"], source_deck["id"])

        await self.invoke("word-flag", str(selected["id"]), "--reason", "typo")
        issues = await self.invoke("issues")
        self.assertEqual(issues["issues"][0]["word_id"], selected["id"])
        card = dict(selected["card"])
        card.pop("source_file", None)
        await self.invoke(
            "word-fix", str(selected["id"]), "--card-json", json.dumps(card)
        )
        self.assertEqual((await self.invoke("issues"))["issues"], [])
        archived = await self.invoke("word-archive", str(selected["id"]))
        await self.invoke(
            "word-restore", str(selected["id"]), "--deck", str(source_deck["id"])
        )
        self.assertNotEqual(archived["deck_id"], source_deck["id"])

        job_list = await self.invoke("job", "list")
        self.assertEqual(len(job_list["jobs"]), 5)
        await self.invoke("job", "disable", "push")
        rejected = await self.invoke("job", "run", "push", succeeds=False)
        self.assertIn("disabled", rejected["error"])
        queued = await self.invoke("job", "run", "push", "--force")
        self.assertEqual(queued["status"], "queued")
        runs = await self.invoke("job", "runs", "--name", "push")
        self.assertEqual(runs["runs"][0]["id"], queued["id"])
        await self.invoke("job", "enable", "push")
        self.assertTrue((await self.invoke("health"))["ok"])

        staged = await self.invoke(
            "propose-words",
            "--deck", "CLI proposals",
            "--cards-json",
            '[{"kind":"other","lemma":"allerdings","translation":"однако",'
            '"example":"Allerdings ist es spät.","pronunciation":"alerdings"}]',
        )
        await self.invoke("confirm-pending", staged["pending_id"])
        rejected = await self.invoke(
            "propose-words",
            "--deck", "CLI proposals",
            "--cards-json",
            '[{"kind":"other","lemma":"ohnehin","translation":"в любом случае",'
            '"example":"Das passiert ohnehin.","pronunciation":"ohnehin"}]',
        )
        await self.invoke("reject-pending", rejected["pending_id"])

        with tempfile.TemporaryDirectory() as tmp:
            exported = Path(tmp) / "deck.csv"
            await self.invoke("export", str(source_deck["id"]), str(exported))
            self.assertTrue(exported.exists())

        await self.invoke("deck", "delete", str(created["id"]))

    def test_training_commands_are_absent_from_parser(self) -> None:
        parser = cli.build_parser()
        for command in (
            "task",
            "task-new",
            "answer",
            "task-context",
            "session",
            "practice",
            "push",
            "push-plan",
            "curator-run",
        ):
            with self.subTest(command=command), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args([command])


if __name__ == "__main__":
    unittest.main()
