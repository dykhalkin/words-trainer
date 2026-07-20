from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cli
from tests.support import temporary_database


class CliParityTests(unittest.IsolatedAsyncioTestCase):
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

        task = await self.invoke("task-new", "--deck", str(source_deck["id"]))
        context = await self.invoke("task-context", task["task_id"])
        self.assertNotIn("expected", context)
        await self.invoke("answer", task["task_id"], "intentionally wrong")
        answered_context = await self.invoke("task-context", task["task_id"])
        self.assertIn("expected", answered_context)
        await self.invoke("word", task["word"])
        await self.invoke("history", "--limit", "5")
        await self.invoke("history", "--word", task["word"], "--limit", "5")
        analysis = await self.invoke("curator-run", "--dry-run")
        self.assertEqual(analysis["user_id"], bootstrapped["users"][0]["id"])
        await self.invoke("due")
        await self.invoke("stats")

        session = await self.invoke(
            "session", "start", "--kind", "micro", "--deck", str(source_deck["id"]),
            "--target-count", "3"
        )
        self.assertEqual(session["target_count"], 3)
        await self.invoke("session", "stop")
        await self.invoke("push", "compose")
        await self.invoke("push", "claim")
        await self.invoke("push-plan", "set", '{"word_ids": []}')
        await self.invoke("push-plan", "get")

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


if __name__ == "__main__":
    unittest.main()
