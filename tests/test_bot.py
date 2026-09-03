from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram.types import Chat, Message, Update, User

from bot.config import Settings, load_settings
from bot.keyboards import (
    AnswerCallback,
    ContextDeckCallback,
    GradeCallback,
    PracticeDeckCallback,
    QuickDeckCallback,
    ReminderCallback,
    StatsDeckCallback,
    WordActionCallback,
    answer_keyboard,
    deck_picker_keyboard,
    post_answer_keyboard,
    youglish_url,
)
from bot.middleware import LearnerMiddleware, update_chat
from bot.presentation import (
    learning_guide_text,
    session_summary,
    stats_text,
    task_text,
    verdict_text,
    youglish_query,
)


class BotPresentationTests(unittest.TestCase):
    def test_callback_payload_is_compact_and_keyboard_keeps_answer_server_side(self) -> None:
        packed = AnswerCallback(task_id="1234567890abcdef", option=3).pack()
        self.assertLessEqual(len(packed.encode()), 64)
        markup = answer_keyboard("1234567890abcdef", ["sehr langes sichtbares Wort"])
        assert markup is not None
        callback_data = markup.inline_keyboard[0][0].callback_data
        self.assertNotIn("sichtbares", callback_data or "")
        typed_markup = answer_keyboard("1234567890abcdef", [])
        self.assertIsNone(typed_markup)
        pre_answer_labels = [
            button.text for row in markup.inline_keyboard for button in row
        ]
        self.assertNotIn("🗄 В архив", pre_answer_labels)
        self.assertNotIn("⚠️ Ошибка", pre_answer_labels)
        post_answer_labels = [
            button.text
            for row in post_answer_keyboard(
                "1234567890abcdef", "auf jeden Fall"
            ).inline_keyboard
            for button in row
        ]
        self.assertEqual(
            post_answer_labels,
            [
                "🎬 В живой речи (YouGlish)",
                "Объяснить",
                "🗄 В архив",
                "⚠️ Ошибка",
            ],
        )
        youglish_button = post_answer_keyboard(
            "1234567890abcdef", "die Straße"
        ).inline_keyboard[0][0]
        self.assertEqual(
            youglish_button.url,
            "https://youglish.com/pronounce/die%20Stra%C3%9Fe/german",
        )
        self.assertIsNone(youglish_button.callback_data)
        payloads = [
            PracticeDeckCallback(deck_id=123456789, page=99).pack(),
            QuickDeckCallback(deck_id=123456789, page=99).pack(),
            ContextDeckCallback(deck_id=123456789, page=99).pack(),
            StatsDeckCallback(deck_id=123456789, page=99).pack(),
            WordActionCallback(
                task_id="1234567890abcdef", action="archive", confirm=1
            ).pack(),
            GradeCallback(
                task_id="1234567890abcdef",
                evaluation_id="f" * 32,
                action="retry",
            ).pack(),
            ReminderCallback(action="cadence", value="1440", revision=999999).pack(),
        ]
        self.assertTrue(all(len(value.encode()) <= 64 for value in payloads))

    def test_youglish_query_removes_learning_metadata(self) -> None:
        self.assertEqual(
            youglish_query({"word": "denken an Akk", "kind": "verb_prep"}),
            "denken an",
        )
        self.assertEqual(
            youglish_query(
                {
                    "word": "es kommt auf … an",
                    "card": {"kind": "phrase"},
                }
            ),
            "es kommt auf an",
        )
        self.assertEqual(
            youglish_query(
                {
                    "word": "es kommt auf … an",
                    "kind": "phrase",
                    "example": "Es kommt auf die Situation an.",
                }
            ),
            "Es kommt auf die Situation an.",
        )
        self.assertEqual(
            youglish_url("auf jeden Fall"),
            "https://youglish.com/pronounce/auf%20jeden%20Fall/german",
        )

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
        quick_markup = deck_picker_keyboard(decks, quick=True)
        quick_payload = quick_markup.inline_keyboard[0][0].callback_data or ""
        self.assertTrue(quick_payload.startswith("qd:"))
        context_markup = deck_picker_keyboard(decks, context_mode=True)
        context_payload = context_markup.inline_keyboard[0][0].callback_data or ""
        self.assertTrue(context_payload.startswith("cd:"))

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
            verdict_text(
                {
                    "correct": False,
                    "expected": "gehen",
                    "note": "Проверьте форму.",
                    "example": "Ich gehe nach Hause.",
                }
            ),
            "❌ Не совсем\nОтвет: gehen\nПроверьте форму.\n\n"
            "Пример: Ich gehe nach Hause.",
        )
        self.assertEqual(
            verdict_text(
                {
                    "correct": False,
                    "word": "die Salbe",
                    "expected": "die Salbe",
                    "note": "Артикль обязателен.",
                    "example": "Die Salbe hilft.",
                }
            ),
            "❌ Не совсем\nСлово: die Salbe\nОтвет: die Salbe\n"
            "Артикль обязателен.\n\nПример для «die Salbe»: Die Salbe hilft.",
        )
        self.assertEqual(
            verdict_text(
                {
                    "correct": True,
                    "kind": "phrase",
                    "word": "auf jeden Fall",
                    "expected": "auf jeden Fall",
                    "example": "Ich komme auf jeden Fall.",
                }
            ),
            "✅ Верно\nФраза: auf jeden Fall\nОтвет: auf jeden Fall\n\n"
            "Пример фразы «auf jeden Fall»: Ich komme auf jeden Fall.",
        )
        tense_verdict = verdict_text(
            {
                "correct": True,
                "kind": "verb",
                "word": "aufbauen",
                "expected": "aufbauen",
                "tense_strip": {
                    "person": "er/es/sie",
                    "forms": [
                        {"tense": "Präsens", "form": "baut auf"},
                        {"tense": "Perfekt", "form": "hat aufgebaut"},
                        {"tense": "Präteritum", "form": "baute auf"},
                    ],
                },
            }
        )
        self.assertIn(
            "Времена (er/es/sie): Präsens: baut auf · "
            "Perfekt: hat aufgebaut · Präteritum: baute auf",
            tense_verdict,
        )
        self.assertEqual(
            session_summary({"answered_count": 5, "correct_count": 4}),
            "Тренировка завершена: 4/5 правильных.",
        )

    def test_stats_names_stages_without_explanations_or_word_lists(self) -> None:
        value = {
            "deck": None,
            "words_total": 2,
            "words_new": 1,
            "words_studied": 1,
            "due_now": 1,
            "today": {"review_attempts": 0, "unique_words": 0},
            "reviews_last_7d": {"accuracy": None, "correct": 0, "count": 0},
            "days": 7,
            "needs_fix": 0,
            "archived": 0,
            "learning_stages": [
                {
                    "key": "new",
                    "name": "Новые",
                    "count": 1,
                },
                {
                    "key": "1",
                    "name": "Знакомство",
                    "count": 1,
                },
            ],
            "daily_activity": [],
        }

        rendered = stats_text(value)

        self.assertNotIn("Этапы 0–3", rendered)
        self.assertIn("Новые: 1", rendered)
        self.assertIn("Знакомство: 1", rendered)
        self.assertNotIn("neu", rendered)
        self.assertNotIn("1–2 успешных повторения", rendered)
        self.assertIn("Ответов: 0", rendered)
        self.assertNotIn(" / ", rendered)

    def test_learning_guide_explains_stages_and_timing(self) -> None:
        rendered = learning_guide_text()

        for stage in (
            "Новые",
            "Повторное изучение",
            "Знакомство",
            "Закрепление",
            "Освоено",
        ):
            self.assertIn(stage, rendered)
        self.assertIn("10 минут", rendered)
        self.assertIn("1 день и 3 дня", rendered)
        self.assertIn("около 8 дней", rendered)
        self.assertIn("3 недель, затем 2 и 6 месяцев", rendered)
        self.assertIn("примерно за 12 дней", rendered)
        self.assertIn("Новые → Знакомство: 1 засчитанный ответ", rendered)
        self.assertIn("нужны ещё 2", rendered)
        self.assertIn("интервал должен достичь 21 дня", rendered)
        self.assertIn("требуется 4 засчитанных ответа", rendered)
        self.assertIn("либо 5 при замедленном", rendered)
        self.assertIn("Любая новая ошибка запускает этот путь заново", rendered)
        self.assertIn(
            "только на этапах «Закрепление» и «Освоено»", rendered
        )
        self.assertIn("Как изучаются фразы", rendered)
        self.assertIn("применить в короткой новой реплике", rendered)
        self.assertIn("Подходящий синоним считается почти правильным", rendered)
        self.assertIn("Команда /context", rendered)
        self.assertIn("не изменяет этап, интервал", rendered)


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

    def test_model_specific_prices_and_explicit_override(self) -> None:
        settings = Settings(
            TELEGRAM_BOT_TOKEN="token",
            DATABASE_URL="postgresql://unused",
            OWNER_CHAT_ID=101,
            TUTOR_MODEL="gpt-5.6-luna",
            CURATOR_MODEL="gpt-5.6-terra",
        )
        self.assertEqual(settings.prices_for("tutor"), (1.0, 6.0))
        self.assertEqual(settings.prices_for("grader"), (1.0, 6.0))
        self.assertEqual(settings.prices_for("curator"), (2.5, 15.0))
        overridden = Settings(
            TELEGRAM_BOT_TOKEN="token",
            DATABASE_URL="postgresql://unused",
            OWNER_CHAT_ID=101,
            TUTOR_MODEL="gpt-5.6-luna",
            TUTOR_INPUT_USD_PER_MILLION=9.0,
        )
        self.assertEqual(overridden.prices_for("tutor"), (9.0, 6.0))


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
