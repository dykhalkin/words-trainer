"""Compact Telegram callback payloads and inline keyboards."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder


class AnswerCallback(CallbackData, prefix="a"):
    task_id: str
    option: int


class StartSessionCallback(CallbackData, prefix="s"):
    delivery_id: int = 0


class ExplainCallback(CallbackData, prefix="e"):
    task_id: str


class PendingCallback(CallbackData, prefix="p"):
    pending_id: str
    accept: int


def answer_keyboard(task_id: str, options: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, option in enumerate(options):
        builder.button(text=option, callback_data=AnswerCallback(task_id=task_id, option=index))
    builder.adjust(1)
    return builder.as_markup()


def start_keyboard(delivery_id: int = 0) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Начать короткую тренировку",
                    callback_data=StartSessionCallback(delivery_id=delivery_id).pack(),
                )
            ]
        ]
    )


def explain_keyboard(task_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Объяснить",
                    callback_data=ExplainCallback(task_id=task_id).pack(),
                )
            ]
        ]
    )


def pending_keyboard(pending_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Добавить",
                    callback_data=PendingCallback(pending_id=pending_id, accept=1).pack(),
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=PendingCallback(pending_id=pending_id, accept=0).pack(),
                ),
            ]
        ]
    )
