"""Deterministic Telegram drill handlers."""

from __future__ import annotations

import json
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from vocab import db, scheduler, words

from .keyboards import AnswerCallback, StartSessionCallback, answer_keyboard, explain_keyboard
from .presentation import session_summary, task_text, verdict_text

router = Router(name="drill")


def _payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def send_task(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    session = await scheduler.get_open_session(database, learner["id"])
    if not session:
        await message.answer("Сначала начните тренировку: /practice")
        return
    task = await scheduler.create_task(database, learner["id"], session_id=session["id"])
    if task is None:
        stopped = await scheduler.stop_session(database, learner["id"])
        await message.answer(session_summary(stopped) + "\nСейчас больше нечего повторять.")
        return
    options = list(task.get("options") or [])
    markup = answer_keyboard(task["task_id"], options) if options else None
    await message.answer(task_text(task), reply_markup=markup)


async def finish_answer(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    task_id: str,
    answer: str,
    *,
    edit: bool,
) -> None:
    verdict = await scheduler.submit_answer(database, learner["id"], task_id, answer)
    if verdict.get("error"):
        if not edit:
            await message.answer("Это упражнение уже неактивно.")
        return
    rendered = verdict_text(verdict)
    if edit:
        original = message.text or "Упражнение"
        await message.edit_text(
            f"{original}\n\n{rendered}", reply_markup=explain_keyboard(task_id)
        )
    else:
        await message.answer(rendered, reply_markup=explain_keyboard(task_id))
    if verdict.get("session_complete"):
        await message.answer(
            session_summary(
                {
                    "answered_count": verdict.get("session_answered_count") or 0,
                    "correct_count": verdict.get("session_correct_count") or 0,
                }
            )
        )
        return
    await send_task(message, database, learner)


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Я помогу регулярно повторять слова.\n\n"
        "/practice — длинная тренировка\n"
        "/decks — колоды\n"
        "/stats — статистика\n"
        "/stop — остановить тренировку"
    )


@router.message(Command("practice"))
async def practice(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    await scheduler.start_session(database, learner["id"], kind="long")
    await message.answer("Начинаем длинную тренировку. /stop — закончить.")
    await send_task(message, database, learner)


@router.message(Command("stop"))
async def stop(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    session = await scheduler.stop_session(database, learner["id"])
    await message.answer(session_summary(session))


@router.message(Command("decks"))
async def decks(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    rows = await words.list_decks(database, learner["id"])
    lines = [f"• {row['name']} ({row['language']}): {row['word_count']}" for row in rows]
    await message.answer("Ваши колоды:\n" + ("\n".join(lines) if lines else "пока пусто"))


@router.message(Command("stats"))
async def stats(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    value = await scheduler.stats(database, learner["id"])
    recent = value["reviews_last_7d"]
    accuracy = recent["accuracy"]
    accuracy_text = "—" if accuracy is None else f"{round(accuracy * 100)}%"
    await message.answer(
        f"Слов: {value['words_total']}\n"
        f"Изучаются: {value['words_studied']}\n"
        f"К повторению: {value['due_now']}\n"
        f"Точность за 7 дней: {accuracy_text}"
    )


@router.callback_query(StartSessionCallback.filter())
async def start_micro(
    callback: CallbackQuery, database: db.Database, learner: dict[str, Any]
) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    await scheduler.start_session(database, learner["id"], kind="micro", target_count=5)
    await callback.message.edit_reply_markup(reply_markup=None)
    await send_task(callback.message, database, learner)


@router.callback_query(AnswerCallback.filter())
async def answer_button(
    callback: CallbackQuery,
    callback_data: AnswerCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    context = await scheduler.task_context(database, learner["id"], callback_data.task_id)
    if not context or context["status"] != "open":
        await callback.answer("Упражнение уже неактивно", show_alert=False)
        return
    options = list(_payload(context["payload"]).get("options") or [])
    if callback_data.option < 0 or callback_data.option >= len(options):
        await callback.answer("Упражнение уже неактивно", show_alert=False)
        return
    await callback.answer()
    await finish_answer(
        callback.message,
        database,
        learner,
        callback_data.task_id,
        str(options[callback_data.option]),
        edit=True,
    )
