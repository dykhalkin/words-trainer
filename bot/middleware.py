"""Private-chat allowlist and persisted learner resolution."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from vocab import db, words

logger = logging.getLogger(__name__)


def update_chat(update: Update) -> tuple[int, str] | None:
    message = update.message or update.edited_message
    if message:
        return message.chat.id, message.chat.type
    callback = update.callback_query
    if callback and callback.message:
        return callback.message.chat.id, callback.message.chat.type
    return None


class LearnerMiddleware(BaseMiddleware):
    def __init__(self, database: db.Database, allowed_chat_ids: frozenset[int]) -> None:
        self.database = database
        self.allowed_chat_ids = allowed_chat_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return await handler(event, data)
        chat = update_chat(event)
        if chat is None:
            return None
        chat_id, chat_type = chat
        if chat_type != "private" or chat_id not in self.allowed_chat_ids:
            logger.warning("dropped unauthorized Telegram update")
            return None
        try:
            learner = await words.get_user(self.database, chat_id)
        except Exception:
            logger.exception("storage unavailable while resolving learner")
            bot = data.get("bot")
            if bot:
                await bot.send_message(chat_id, "Хранилище временно недоступно. Попробуйте позже.")
            return None
        if learner is None:
            logger.error("allowlisted chat has no persisted user")
            return None
        data["learner"] = learner
        data["database"] = self.database
        return await handler(event, data)
