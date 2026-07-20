"""Free-form tutor, explanation, and staged-card confirmation handlers."""

from __future__ import annotations

import json
import logging
from typing import Any

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from vocab import db, llm, scheduler, words

from .agent import TutorService
from .drill import finish_answer
from .keyboards import ExplainCallback, PendingCallback, pending_keyboard

logger = logging.getLogger(__name__)
router = Router(name="chat")


def proposal_text(row: dict[str, Any]) -> str:
    raw = row["cards"]
    cards = json.loads(raw) if isinstance(raw, str) else list(raw)
    lines = [f"Предложение для колоды «{row['deck_name']}»:" ]
    for card in cards:
        lines.append(f"• {card['lemma']} — {card['translation']}")
    lines.append("\nДобавить эти карточки?")
    return "\n".join(lines)


async def send_tutor_reply(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    tutor: TutorService,
    prompt: str,
) -> None:
    if not tutor.available:
        await message.answer(
            "Преподаватель сейчас недоступен: OpenAI не настроен. Тренировки работают как обычно."
        )
        return
    try:
        reply = await tutor.reply(database, learner, prompt)
    except llm.BudgetExceeded:
        await message.answer("Лимит расходов на преподавателя в этом месяце исчерпан.")
        return
    except Exception:
        logger.exception("tutor call failed")
        await message.answer("Преподаватель временно недоступен. Тренировки продолжают работать.")
        return
    await message.answer(reply.text)
    for proposal in reply.proposals:
        row = await words.get_pending(database, learner["id"], proposal["pending_id"])
        if row:
            await message.answer(
                proposal_text(row), reply_markup=pending_keyboard(row["id"])
            )


@router.message(F.text & ~F.text.startswith("/"))
async def text_answer_or_chat(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    tutor: TutorService,
) -> None:
    task = await scheduler.get_open_task(database, learner["id"])
    if task:
        await finish_answer(
            message, database, learner, task["task_id"], message.text or "", edit=False
        )
        return
    await send_tutor_reply(message, database, learner, tutor, message.text or "")


@router.callback_query(ExplainCallback.filter())
async def explain(
    callback: CallbackQuery,
    callback_data: ExplainCallback,
    database: db.Database,
    learner: dict[str, Any],
    tutor: TutorService,
) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    context = await scheduler.task_context(database, learner["id"], callback_data.task_id)
    if not context or context["status"] != "answered":
        await callback.message.answer("Контекст упражнения уже недоступен.")
        return
    await send_tutor_reply(
        callback.message,
        database,
        learner,
        tutor,
        "Объясни мой последний результат упражнения, используя current_task для task_id "
        + callback_data.task_id,
    )


@router.callback_query(PendingCallback.filter())
async def resolve_pending(
    callback: CallbackQuery,
    callback_data: PendingCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    await callback.answer()
    if not isinstance(callback.message, Message):
        return
    try:
        if callback_data.accept:
            result = await words.commit_pending(
                database, learner["id"], callback_data.pending_id
            )
            text = f"Добавлено карточек: {result['inserted']}; уже было: {result['reused']}."
        else:
            await words.reject_pending(database, learner["id"], callback_data.pending_id)
            text = "Предложение отклонено."
    except LookupError:
        text = "Предложение уже обработано."
    await callback.message.edit_text(text, reply_markup=None)
