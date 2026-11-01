from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.chat import handle_card_feedback


class CardFeedbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_note_is_saved_without_tutor_and_training_resumes(self):
        message = SimpleNamespace(text="Неверный перевод", answer=AsyncMock())
        database, generator = object(), object()
        pending = {"kind": "card_feedback", "payload": {"task_id": "task"}}
        with (
            patch("bot.chat.reminders.get_pending_action", AsyncMock(return_value=pending)),
            patch("bot.chat.reminders.clear_pending_action", AsyncMock()) as clear,
            patch("bot.chat.words.save_card_feedback", AsyncMock(return_value={"id": 1})) as save,
            patch("bot.chat._resume_training_if_needed", AsyncMock()) as resume,
        ):
            self.assertTrue(await handle_card_feedback(message, database, {"id": 7}, generator))
            save.assert_awaited_once_with(database, 7, "task", reason="Неверный перевод")
            clear.assert_awaited_once_with(database, 7)
            resume.assert_awaited_once()
            self.assertIn("до отдельного", message.answer.call_args.args[0])

    async def test_invalid_note_keeps_pending_report(self):
        message = SimpleNamespace(text="?", answer=AsyncMock())
        with (
            patch("bot.chat.reminders.get_pending_action", AsyncMock(return_value={"kind": "card_feedback"})),
            patch("bot.chat.reminders.clear_pending_action", AsyncMock()) as clear,
            patch("bot.chat.words.save_card_feedback", AsyncMock()) as save,
        ):
            self.assertTrue(await handle_card_feedback(message, object(), {"id": 7}, object()))
            clear.assert_not_awaited()
            save.assert_not_awaited()

    async def test_resolved_report_clears_stale_pending_note(self):
        message = SimpleNamespace(text="Неверный перевод", answer=AsyncMock())
        with (
            patch("bot.chat.reminders.get_pending_action", AsyncMock(return_value={"kind": "card_feedback", "payload": {"task_id": "task"}})),
            patch("bot.chat.reminders.clear_pending_action", AsyncMock()) as clear,
            patch("bot.chat.words.save_card_feedback", AsyncMock(return_value=None)),
        ):
            self.assertTrue(await handle_card_feedback(message, object(), {"id": 7}, object()))
            clear.assert_awaited_once()
            self.assertIn("уже обработана", message.answer.call_args.args[0])
