"""Persistently claimed proactive Telegram pushes."""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot

from vocab import db, scheduler, words

from .keyboards import start_keyboard

logger = logging.getLogger(__name__)


async def run_push_cycle(bot: Bot, database: db.Database) -> int:
    sent = 0
    for learner in await words.list_users(database):
        delivery = await scheduler.claim_push(database, learner["id"])
        if not delivery:
            continue
        payload: dict[str, Any] = delivery["payload"]
        count = len(payload.get("word_ids") or [])
        try:
            message = await bot.send_message(
                learner["chat_id"],
                f"Пора повторить {count} слов. Это займёт пару минут.",
                reply_markup=start_keyboard(delivery["id"]),
            )
        except Exception as exc:
            logger.exception("push delivery failed", extra={"delivery_id": delivery["id"]})
            await scheduler.mark_delivery_failed(database, delivery["id"], str(exc))
        else:
            await scheduler.mark_delivery_sent(database, delivery["id"], message.message_id)
            sent += 1
    return sent
