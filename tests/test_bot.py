from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User

from bot.config import load_settings
from bot.keyboards import (
    AnswerCallback,
    PracticeDeckCallback,
    StatsDeckCallback,
    WordActionCallback,
    answer_keyboard,
    deck_picker_keyboard,
)
from bot.middleware import LearnerMiddleware, update_chat
from bot.presentation import session_summary, task_text, verdict_text


class BotPresentationTests(unittest.TestCase):
    def test_callback_payload_is_compact_and_keyboard_keeps_answer_server_side(self) -> None:
        packed = AnswerCallback(task_id="1234567890abcdef", option=3).pack()
        self.assertLessEqual(len(packed.encode()), 64)
        markup = answer_keyboard("1234567890abcdef", ["sehr langes sichtbares Wort"])
        callback_data = markup.inline_keyboard[0][0].callback_data
        self.assertNotIn("sichtbares", callback_data or "")
        typed_markup = answer_keyboard("1234567890abcdef", [])
        self.assertEqual(len(typed_markup.inline_keyboard), 1)
        payloads = [
            PracticeDeckCallback(deck_id=123456789, page=99).pack(),
            StatsDeckCallback(deck_id=123456789, page=99).pack(),
            WordActionCallback(
                task_id="1234567890abcdef", action="archive", confirm=1
            ).pack(),
        ]
        self.assertTrue(all(len(value.encode()) <= 64 for value in payloads))

    def test_archive_is_absent_from_deck_picker(self) -> None:
        decks = [
            {
                "id": 1,
                "name": "A2",
                "language": "de",
                "active_word_count": 3,
                "is_archive": False,
            },
            {
                "id": 2,
                "name": "Archive",
                "language": "de",
                "active_word_count": 0,
                "is_archive": True,
            },
        ]
        markup = deck_picker_keyboard(decks)
        labels = [button.text for row in markup.inline_keyboard for button in row]
        self.assertIn("A2 (de) · 3", labels)
        self.assertNotIn("Archive", labels)

    def test_rendering_for_typed_task_and_verdict(self) -> None:
        self.assertIn(
            "Напишите ответ",
            task_text({"prompt": "Переведите", "hint": "глагол"}),
        )
        self.assertEqual(
            verdict_text({"correct": True, "expected": "gehen", "note": None}),
            "✅ Верно\nОтвет: gehen",
        )
        self.assertEqual(
            session_summary({"answered_count": 5, "correct_count": 4}),
            "Тренировка завершена: 4/5 правильных.",
        )


class ConfigTests(unittest.TestCase):
    def test_explicit_env_file_and_optional_spouse(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "env"
            env_path.write_text(
                "TELEGRAM_BOT_TOKEN=test-token\n"
                "DATABASE_URL=postgresql://example/test\n"
                "OWNER_CHAT_ID=101\n",
                encoding="utf-8",
            )
            names = ("WORDSBOT_ENV_FILE", "TELEGRAM_BOT_TOKEN", "DATABASE_URL", "OWNER_CHAT_ID", "SPOUSE_CHAT_ID")
            previous = {name: os.environ.pop(name, None) for name in names}
            try:
                os.environ["WORDSBOT_ENV_FILE"] = str(env_path)
                settings = load_settings()
                self.assertEqual(settings.allowed_chat_ids, frozenset({101}))
                self.assertEqual(settings.telegram_bot_token.get_secret_value(), "test-token")
            finally:
                for name in names:
                    os.environ.pop(name, None)
                    if previous[name] is not None:
                        os.environ[name] = previous[name]  # type: ignore[assignment]


class AllowlistTests(unittest.IsolatedAsyncioTestCase):
    async def test_foreign_private_update_is_dropped_without_database_read(self) -> None:
        database = AsyncMock()
        middleware = LearnerMiddleware(database, frozenset({101}))
        handler = AsyncMock()
        message = Message(
            message_id=1,
            date=0,
            chat=Chat(id=202, type="private"),
            from_user=User(id=202, is_bot=False, first_name="X"),
            text="hello",
        )
        update = Update(update_id=1, message=message)
        self.assertEqual(update_chat(update), (202, "private"))
        result = await middleware(handler, update, {})
        self.assertIsNone(result)
        handler.assert_not_awaited()
        database.connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
