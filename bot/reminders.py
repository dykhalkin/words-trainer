"""Learner-owned Telegram reminder policy configuration."""

from __future__ import annotations

from datetime import time
from typing import Any
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from vocab import db, reminders, scheduler

from .keyboards import (
    ReminderCallback,
    reminder_confirmation_keyboard,
    reminder_days_keyboard,
    reminder_frequency_keyboard,
    reminder_keyboard,
)

router = Router(name="reminders")
DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def _days_text(mask: int) -> str:
    if mask == 127:
        return "каждый день"
    if mask == 31:
        return "Пн–Пт"
    return ", ".join(label for index, label in enumerate(DAY_NAMES) if mask & (1 << index))


def _cadence_text(minutes: int) -> str:
    if minutes == 1440:
        return "примерно раз в день"
    hours = minutes // 60
    return f"примерно раз в {hours} ч"


def _time_text(value: time | str) -> str:
    parsed = value if isinstance(value, time) else time.fromisoformat(value)
    return parsed.strftime("%H:%M")


async def policy_text(database: db.Database, user_id: int) -> tuple[str, dict[str, Any]]:
    policy = await reminders.get_policy(database, user_id)
    if not policy:
        raise LookupError("reminder policy not found")
    upcoming = await reminders.list_upcoming(database, user_id)
    outcome = await reminders.last_outcome(database, user_id)
    zone = ZoneInfo(policy["timezone"])
    planned = ", ".join(
        row["scheduled_for"].astimezone(zone).strftime("%a %H:%M") for row in upcoming
    ) or "пока нет"
    outcome_text = "нет"
    if outcome:
        when = outcome["sent_at"] or outcome["scheduled_for"]
        when_text = when.astimezone(zone).strftime("%d.%m %H:%M") if when else "—"
        outcome_text = f"{outcome['status']} · {when_text}"
        if outcome.get("skip_reason"):
            outcome_text += f" · {outcome['skip_reason']}"
    text = (
        f"🔔 Умные напоминания: {'включены' if policy['mode'] == 'smart' else 'выключены'}\n"
        f"Часовой пояс: {policy['timezone']}\n"
        f"Дни: {_days_text(policy['days_mask'])}\n"
        f"Окно: {_time_text(policy['window_start'])}–{_time_text(policy['window_end'])}\n"
        f"Частота: {_cadence_text(policy['target_interval_minutes'])} "
        f"(не более {reminders.daily_cap(policy)} в день)\n"
        f"Запланировано curator-ом: {planned}\n"
        f"Последний результат: {outcome_text}\n\n"
        "Это верхняя граница частоты, а не гарантированное число сообщений. "
        "Curator выбирает полезные моменты и может прислать реже, если вы уже "
        "тренировались или повторять нечего."
    )
    return text, policy


async def _show(message: Message, database: db.Database, user_id: int, *, edit: bool) -> None:
    text, policy = await policy_text(database, user_id)
    if edit:
        await message.edit_text(text, reply_markup=reminder_keyboard(policy))
    else:
        await message.answer(text, reply_markup=reminder_keyboard(policy))


async def _stage_confirmation(
    database: db.Database,
    user_id: int,
    revision: int,
    changes: dict[str, Any],
    message: Message,
    *,
    edit: bool = True,
) -> None:
    policy = await reminders.get_policy(database, user_id)
    if not policy or policy["revision"] != revision:
        sender = message.edit_text if edit else message.answer
        await sender("Настройки изменились. Откройте /reminders ещё раз.")
        return
    preview = {**policy, **changes}
    try:
        reminders.validate_policy_values(
            days_mask=preview["days_mask"],
            window_start=preview["window_start"],
            window_end=preview["window_end"],
            target_interval_minutes=preview["target_interval_minutes"],
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await reminders.set_pending_action(
        database,
        user_id,
        "reminder_confirm",
        {"revision": revision, "changes": changes},
    )
    summary = (
        "Подтвердите настройки:\n"
        f"Режим: {'включён' if preview['mode'] == 'smart' else 'выключен'}\n"
        f"Дни: {_days_text(preview['days_mask'])}\n"
        f"Окно: {_time_text(preview['window_start'])}–{_time_text(preview['window_end'])}\n"
        f"Частота: {_cadence_text(preview['target_interval_minutes'])} "
        f"(не более {reminders.daily_cap(preview)} в день)"
    )
    sender = message.edit_text if edit else message.answer
    await sender(summary, reply_markup=reminder_confirmation_keyboard(revision))


@router.message(Command("reminders"))
async def show_reminders(
    message: Message, database: db.Database, learner: dict[str, Any]
) -> None:
    await _show(message, database, learner["id"], edit=False)


@router.callback_query(ReminderCallback.filter())
async def reminder_action(
    callback: CallbackQuery,
    callback_data: ReminderCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    policy = await reminders.get_policy(database, learner["id"])
    if not policy or policy["revision"] != callback_data.revision:
        await callback.answer("Настройки уже изменились")
        await _show(callback.message, database, learner["id"], edit=True)
        return
    action = callback_data.action
    await callback.answer()
    if action == "cancel":
        await reminders.clear_pending_action(database, learner["id"])
        await _show(callback.message, database, learner["id"], edit=True)
        return
    if action == "mode":
        await _stage_confirmation(
            database,
            learner["id"],
            policy["revision"],
            {"mode": callback_data.value},
            callback.message,
        )
        return
    if action == "days":
        await reminders.set_pending_action(
            database,
            learner["id"],
            "reminder_days",
            {"revision": policy["revision"], "days_mask": policy["days_mask"]},
        )
        await callback.message.edit_text(
            "Выберите активные дни:",
            reply_markup=reminder_days_keyboard(policy["days_mask"], policy["revision"]),
        )
        return
    if action == "day":
        pending = await reminders.get_pending_action(database, learner["id"])
        if not pending or pending["kind"] != "reminder_days":
            await _show(callback.message, database, learner["id"], edit=True)
            return
        mask = int(pending["payload"]["days_mask"]) ^ (1 << int(callback_data.value))
        if mask == 0:
            await callback.message.answer("Нужно оставить хотя бы один активный день.")
            return
        pending["payload"]["days_mask"] = mask
        await reminders.set_pending_action(
            database, learner["id"], "reminder_days", pending["payload"]
        )
        await callback.message.edit_reply_markup(
            reply_markup=reminder_days_keyboard(mask, policy["revision"])
        )
        return
    if action == "days_done":
        pending = await reminders.get_pending_action(database, learner["id"])
        if not pending or pending["kind"] != "reminder_days":
            await _show(callback.message, database, learner["id"], edit=True)
            return
        await _stage_confirmation(
            database,
            learner["id"],
            policy["revision"],
            {"days_mask": int(pending["payload"]["days_mask"])},
            callback.message,
        )
        return
    if action == "window":
        if await scheduler.get_open_task(database, learner["id"]):
            await callback.message.answer("Сначала завершите текущее упражнение.")
            return
        await reminders.set_pending_action(
            database,
            learner["id"],
            "reminder_window_start",
            {"revision": policy["revision"]},
        )
        await callback.message.edit_text("Введите начало окна в формате HH:MM.")
        return
    if action == "frequency":
        await callback.message.edit_text(
            "Выберите примерную частоту:",
            reply_markup=reminder_frequency_keyboard(policy["revision"]),
        )
        return
    if action == "cadence":
        await _stage_confirmation(
            database,
            learner["id"],
            policy["revision"],
            {"target_interval_minutes": int(callback_data.value)},
            callback.message,
        )
        return
    if action == "cadence_custom":
        if await scheduler.get_open_task(database, learner["id"]):
            await callback.message.answer("Сначала завершите текущее упражнение.")
            return
        await reminders.set_pending_action(
            database,
            learner["id"],
            "reminder_cadence",
            {"revision": policy["revision"]},
        )
        await callback.message.edit_text("Введите целое число часов от 1 до 24.")
        return
    if action == "confirm":
        pending = await reminders.get_pending_action(database, learner["id"])
        if not pending or pending["kind"] != "reminder_confirm":
            await _show(callback.message, database, learner["id"], edit=True)
            return
        changes = dict(pending["payload"]["changes"])
        for field in ("window_start", "window_end"):
            if field in changes:
                changes[field] = time.fromisoformat(changes[field])
        try:
            await reminders.update_policy(
                database,
                learner["id"],
                expected_revision=policy["revision"],
                **changes,
            )
        except ValueError:
            await callback.message.edit_text("Настройки изменились. Откройте /reminders снова.")
            return
        await reminders.clear_pending_action(database, learner["id"])
        await _show(callback.message, database, learner["id"], edit=True)
        return
    if action == "replan":
        await reminders.request_replan(database, learner["id"])
        await _show(callback.message, database, learner["id"], edit=True)


async def handle_pending_text(
    message: Message, database: db.Database, learner: dict[str, Any]
) -> bool:
    pending = await reminders.get_pending_action(database, learner["id"])
    if not pending or pending["kind"] not in {
        "reminder_window_start",
        "reminder_window_end",
        "reminder_cadence",
    }:
        return False
    value = (message.text or "").strip()
    revision = int(pending["payload"]["revision"])
    if pending["kind"] == "reminder_cadence":
        try:
            hours = int(value)
            if not 1 <= hours <= 24:
                raise ValueError
        except ValueError:
            await message.answer("Введите целое число часов от 1 до 24.")
            return True
        await _stage_confirmation(
            database,
            learner["id"],
            revision,
            {"target_interval_minutes": hours * 60},
            message,
            edit=False,
        )
        return True
    try:
        parsed = time.fromisoformat(value)
        if parsed.second or parsed.microsecond:
            raise ValueError
    except ValueError:
        await message.answer("Введите время в формате HH:MM, например 09:00.")
        return True
    if pending["kind"] == "reminder_window_start":
        await reminders.set_pending_action(
            database,
            learner["id"],
            "reminder_window_end",
            {"revision": revision, "window_start": parsed.strftime("%H:%M")},
        )
        await message.answer("Теперь введите конец окна в формате HH:MM.")
        return True
    await _stage_confirmation(
        database,
        learner["id"],
        revision,
        {
            "window_start": pending["payload"]["window_start"],
            "window_end": parsed.strftime("%H:%M"),
        },
        message,
        edit=False,
    )
    return True
