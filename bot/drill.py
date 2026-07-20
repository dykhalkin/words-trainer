"""Deterministic Telegram drill handlers."""

from __future__ import annotations

import json
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from vocab import db, scheduler, statistics, words

from .keyboards import (
    AnswerCallback,
    IssuesCallback,
    PracticeDeckCallback,
    StartSessionCallback,
    StatsDeckCallback,
    WordActionCallback,
    answer_keyboard,
    confirm_word_action_keyboard,
    deck_picker_keyboard,
    explain_keyboard,
    issues_keyboard,
)
from .presentation import session_summary, stats_text, task_text, verdict_text

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
    markup = answer_keyboard(task["task_id"], options)
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
        "/issues — карточки, требующие исправления\n"
        "/stop — остановить тренировку"
    )


@router.message(Command("practice"))
async def practice(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    decks = [row for row in await words.list_decks(database, learner["id"]) if not row["is_archive"]]
    await message.answer(
        "Выберите колоду для тренировки:", reply_markup=deck_picker_keyboard(decks)
    )


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
    value = await statistics.stats(database, learner["id"])
    decks = [row for row in await words.list_decks(database, learner["id"]) if not row["is_archive"]]
    await message.answer(
        stats_text(value), reply_markup=deck_picker_keyboard(decks, stats=True)
    )


async def _issues_page(database: db.Database, learner: dict[str, Any], page: int) -> tuple[str, Any]:
    page_size = 8
    rows = await words.list_word_issues(
        database, learner["id"], limit=page_size + 1, offset=page * page_size
    )
    visible = rows[:page_size]
    lines = ["Карточки, требующие исправления:"]
    lines.extend(
        f"• #{row['word_id']} {row['lemma']} — {row['deck_name']}\n"
        f"  {row['needs_fix_reason'] or 'причина не указана'}"
        for row in visible
    )
    if not visible:
        lines.append("нет")
    return "\n".join(lines), issues_keyboard(page, len(rows) > page_size)


@router.message(Command("issues"))
async def issues(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    text, markup = await _issues_page(database, learner, 0)
    await message.answer(text, reply_markup=markup)


@router.callback_query(PracticeDeckCallback.filter())
async def choose_practice_deck(
    callback: CallbackQuery,
    callback_data: PracticeDeckCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    decks = [row for row in await words.list_decks(database, learner["id"]) if not row["is_archive"]]
    if callback_data.deck_id == -1:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=deck_picker_keyboard(decks, page=callback_data.page)
        )
        return
    deck_id = callback_data.deck_id or None
    try:
        await scheduler.start_session(database, learner["id"], kind="long", deck_id=deck_id)
    except (LookupError, ValueError):
        await callback.answer("Колода больше недоступна")
        return
    await callback.answer()
    await callback.message.edit_text("Тренировка началась. /stop — закончить.", reply_markup=None)
    await send_task(callback.message, database, learner)


@router.callback_query(StatsDeckCallback.filter())
async def choose_stats_deck(
    callback: CallbackQuery,
    callback_data: StatsDeckCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    decks = [row for row in await words.list_decks(database, learner["id"]) if not row["is_archive"]]
    if callback_data.deck_id == -1:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=deck_picker_keyboard(decks, page=callback_data.page, stats=True)
        )
        return
    try:
        value = await statistics.stats(
            database, learner["id"], deck_id=callback_data.deck_id or None
        )
    except LookupError:
        await callback.answer("Колода больше недоступна")
        return
    await callback.answer()
    await callback.message.edit_text(
        stats_text(value), reply_markup=deck_picker_keyboard(decks, stats=True)
    )


@router.callback_query(IssuesCallback.filter())
async def page_issues(
    callback: CallbackQuery,
    callback_data: IssuesCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    await callback.answer()
    if isinstance(callback.message, Message):
        text, markup = await _issues_page(database, learner, callback_data.page)
        await callback.message.edit_text(text, reply_markup=markup)


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


@router.callback_query(WordActionCallback.filter())
async def word_action(
    callback: CallbackQuery,
    callback_data: WordActionCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    context = await scheduler.task_context(database, learner["id"], callback_data.task_id)
    if not context or context["status"] != "open":
        await callback.answer("Упражнение уже неактивно")
        return
    options = list(_payload(context["payload"]).get("options") or [])
    if callback_data.action == "cancel":
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=answer_keyboard(callback_data.task_id, options)
        )
        return
    if not callback_data.confirm:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=confirm_word_action_keyboard(
                callback_data.task_id, callback_data.action
            )
        )
        return
    if callback_data.action == "archive":
        result = await words.archive_task_word(
            database, learner["id"], callback_data.task_id
        )
        message = "Карточка перенесена в архив."
    elif callback_data.action == "flag":
        result = await words.flag_task_word(
            database, learner["id"], callback_data.task_id
        )
        message = "Карточка помечена для исправления."
    else:
        result = None
        message = "Неизвестное действие."
    if not result:
        await callback.answer("Упражнение уже неактивно")
        return
    await callback.answer()
    original = callback.message.text or "Упражнение"
    await callback.message.edit_text(f"{original}\n\n{message}", reply_markup=None)
    await send_task(callback.message, database, learner)
