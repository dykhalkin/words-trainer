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


class PracticeDeckCallback(CallbackData, prefix="pd"):
    deck_id: int
    page: int = 0


class StatsDeckCallback(CallbackData, prefix="sd"):
    deck_id: int
    page: int = 0


class WordActionCallback(CallbackData, prefix="wa"):
    task_id: str
    action: str
    confirm: int = 0


class IssuesCallback(CallbackData, prefix="i"):
    page: int


class GradeCallback(CallbackData, prefix="g"):
    task_id: str
    evaluation_id: str
    action: str


class ReminderCallback(CallbackData, prefix="r"):
    action: str
    value: str = ""
    revision: int = 0


def answer_keyboard(task_id: str, options: list[str]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for index, option in enumerate(options):
        builder.button(text=option, callback_data=AnswerCallback(task_id=task_id, option=index))
    builder.adjust(1)
    builder.row(
        InlineKeyboardButton(
            text="🗄 В архив",
            callback_data=WordActionCallback(
                task_id=task_id, action="archive", confirm=0
            ).pack(),
        ),
        InlineKeyboardButton(
            text="⚠️ Ошибка",
            callback_data=WordActionCallback(
                task_id=task_id, action="flag", confirm=0
            ).pack(),
        ),
    )
    return builder.as_markup()


def confirm_word_action_keyboard(task_id: str, action: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=WordActionCallback(
                        task_id=task_id, action=action, confirm=1
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=WordActionCallback(
                        task_id=task_id, action="cancel", confirm=1
                    ).pack(),
                ),
            ]
        ]
    )


def deck_picker_keyboard(
    decks: list[dict], *, page: int = 0, stats: bool = False, page_size: int = 6
) -> InlineKeyboardMarkup:
    decks = [deck for deck in decks if not deck.get("is_archive", False)]
    callback_type = StatsDeckCallback if stats else PracticeDeckCallback
    start = page * page_size
    visible = decks[start : start + page_size]
    rows: list[list[InlineKeyboardButton]] = []
    if page == 0:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Все активные колоды" if not stats else "Общая статистика",
                    callback_data=callback_type(deck_id=0, page=0).pack(),
                )
            ]
        )
    rows.extend(
        [
            InlineKeyboardButton(
                text=(
                    f"{deck['name']} ({deck['language']})"
                    + (
                        f" · {deck['active_word_count']}"
                        if "active_word_count" in deck
                        else ""
                    )
                ),
                callback_data=callback_type(deck_id=deck["id"], page=page).pack(),
            )
        ]
        for deck in visible
    )
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(
            InlineKeyboardButton(
                text="←", callback_data=callback_type(deck_id=-1, page=page - 1).pack()
            )
        )
    if start + page_size < len(decks):
        navigation.append(
            InlineKeyboardButton(
                text="→", callback_data=callback_type(deck_id=-1, page=page + 1).pack()
            )
        )
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def issues_keyboard(page: int, has_next: bool) -> InlineKeyboardMarkup | None:
    buttons: list[InlineKeyboardButton] = []
    if page > 0:
        buttons.append(
            InlineKeyboardButton(text="←", callback_data=IssuesCallback(page=page - 1).pack())
        )
    if has_next:
        buttons.append(
            InlineKeyboardButton(text="→", callback_data=IssuesCallback(page=page + 1).pack())
        )
    return InlineKeyboardMarkup(inline_keyboard=[buttons]) if buttons else None


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


def grading_failure_keyboard(task_id: str, evaluation_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↻ Проверить снова",
                    callback_data=GradeCallback(
                        task_id=task_id,
                        evaluation_id=evaluation_id,
                        action="retry",
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Засчитать неверным",
                    callback_data=GradeCallback(
                        task_id=task_id,
                        evaluation_id=evaluation_id,
                        action="wrong",
                    ).pack(),
                ),
            ]
        ]
    )


def reminder_keyboard(policy: dict) -> InlineKeyboardMarkup:
    revision = int(policy["revision"])
    mode = "off" if policy["mode"] == "smart" else "smart"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔕 Выключить" if policy["mode"] == "smart" else "🔔 Включить",
                    callback_data=ReminderCallback(
                        action="mode", value=mode, revision=revision
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Дни",
                    callback_data=ReminderCallback(action="days", revision=revision).pack(),
                ),
                InlineKeyboardButton(
                    text="Окно",
                    callback_data=ReminderCallback(action="window", revision=revision).pack(),
                ),
                InlineKeyboardButton(
                    text="Частота",
                    callback_data=ReminderCallback(action="frequency", revision=revision).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↻ Перепланировать",
                    callback_data=ReminderCallback(action="replan", revision=revision).pack(),
                )
            ],
        ]
    )


def reminder_days_keyboard(days_mask: int, revision: int) -> InlineKeyboardMarkup:
    labels = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
    buttons = [
        InlineKeyboardButton(
            text=("✅ " if days_mask & (1 << index) else "▫️ ") + label,
            callback_data=ReminderCallback(
                action="day", value=str(index), revision=revision
            ).pack(),
        )
        for index, label in enumerate(labels)
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            buttons[:4],
            buttons[4:],
            [
                InlineKeyboardButton(
                    text="Готово",
                    callback_data=ReminderCallback(
                        action="days_done", revision=revision
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=ReminderCallback(
                        action="cancel", revision=revision
                    ).pack(),
                ),
            ],
        ]
    )


def reminder_frequency_keyboard(revision: int) -> InlineKeyboardMarkup:
    values = (("Раз в день", 1440), ("2 ч", 120), ("3 ч", 180), ("4 ч", 240), ("6 ч", 360))
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=ReminderCallback(
                    action="cadence", value=str(value), revision=revision
                ).pack(),
            )
        ]
        for label, value in values
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="Другое…",
                callback_data=ReminderCallback(
                    action="cadence_custom", revision=revision
                ).pack(),
            ),
            InlineKeyboardButton(
                text="Отмена",
                callback_data=ReminderCallback(action="cancel", revision=revision).pack(),
            ),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def reminder_confirmation_keyboard(revision: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Подтвердить",
                    callback_data=ReminderCallback(
                        action="confirm", revision=revision
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=ReminderCallback(
                        action="cancel", revision=revision
                    ).pack(),
                ),
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
