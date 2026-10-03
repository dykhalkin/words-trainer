"""Deterministic Telegram drill handlers."""

from __future__ import annotations

import json
import logging
from html import escape
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from vocab import db, llm, reminders, scheduler, statistics, words

from .context import ContextExerciseService
from .grader import TutorGraderService

from .keyboards import (
    AnswerCallback,
    ContextDeckCallback,
    IssuesCallback,
    PracticeDeckCallback,
    QuickDeckCallback,
    StartSessionCallback,
    StatsDeckCallback,
    WordActionCallback,
    GradeCallback,
    FlowCallback,
    skip_keyboard,
    answer_keyboard,
    confirm_word_action_keyboard,
    deck_picker_keyboard,
    grading_failure_keyboard,
    issues_keyboard,
    post_answer_keyboard,
)
from .presentation import (
    learning_guide_text,
    telegram_text_chunks,
    session_summary,
    stats_text,
    task_text,
    verdict_text,
    youglish_query,
)

router = Router(name="drill")
logger = logging.getLogger(__name__)


def _payload(value: Any) -> dict[str, Any]:
    return json.loads(value) if isinstance(value, str) else dict(value or {})


async def send_task(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService | None = None,
    *,
    previous_task_id: str | None = None,
) -> None:
    session = await scheduler.get_open_session(database, learner["id"])
    if not session:
        await message.answer(
            "Сначала начните тренировку: /practice, /quick или /context"
        )
        return
    status: Message | None = None
    if session["kind"] == "context":
        status = await message.answer("Создаю новый контекст…")
        if context_generator is None or not context_generator.available:
            await scheduler.stop_session(
                database, learner["id"], session_id=session["id"]
            )
            await status.edit_text("Контекстная тренировка недоступна: OpenAI не настроен.")
            return
        try:
            task = await context_generator.create_task(
                database, learner, session["id"], previous_task_id=previous_task_id
            )
        except llm.BudgetExceeded:
            await scheduler.stop_session(
                database, learner["id"], session_id=session["id"]
            )
            await status.edit_text(
                "Лимит LLM на этот месяц исчерпан. Контекстная тренировка остановлена."
            )
            return
        except Exception:
            logger.exception("context exercise generation failed")
            await scheduler.stop_session(
                database, learner["id"], session_id=session["id"]
            )
            await status.edit_text(
                "Не удалось создать упражнение на различия. Повторите /contrast "
                "или снова нажмите «Различия · 3 задания»."
                if session.get("comparison")
                else "Не удалось создать контекст. Попробуйте снова командой /context."
            )
            return
    else:
        task = await scheduler.create_task(
            database,
            learner["id"],
            session_id=session["id"],
            previous_task_id=previous_task_id,
        )
    if task and task.get("already_active"):
        if status:
            await status.edit_text("Следующее упражнение уже выдано.")
        return
    if task is None:
        if scheduler.quick_session_expired(session):
            text = session_summary(session) + "\nПять минут истекли."
        else:
            stopped = await scheduler.stop_session(
                database, learner["id"], session_id=session["id"]
            )
            text = session_summary(stopped) + "\nСейчас больше нечего повторять."
        if status:
            await status.edit_text(text)
        else:
            await message.answer(text)
        return
    options = list(task.get("options") or [])
    markup = answer_keyboard(task["task_id"], options) or skip_keyboard(task["task_id"])
    if status:
        await status.edit_text(task_text(task), reply_markup=markup)
    else:
        await message.answer(task_text(task), reply_markup=markup)


async def finish_answer(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    task_id: str,
    answer: str,
    *,
    edit: bool,
    grader: TutorGraderService | None = None,
    context_generator: ContextExerciseService | None = None,
) -> None:
    verdict = await scheduler.begin_answer_submission(
        database, learner["id"], task_id, answer
    )
    if verdict.get("error"):
        if verdict["error"] == "session expired":
            await message.answer(
                session_summary(verdict.get("session")) + "\nПять минут истекли."
            )
            return
        if not edit:
            await message.answer("Это упражнение уже неактивно.")
        return
    if verdict.get("pending"):
        if verdict.get("in_progress"):
            await message.answer("Ответ уже проверяется.")
            return
        status = await message.answer("Проверяю ответ…")
        if grader is None or not grader.available:
            await scheduler.fail_tutor_evaluation(
                database,
                learner["id"],
                verdict["evaluation_id"],
                error="OpenAI is not configured",
                model=grader.model if grader else "unconfigured",
            )
            await status.edit_text(
                "Не удалось проверить ответ. Можно повторить проверку, изменить ответ "
                "или перейти дальше без оценки.",
                reply_markup=grading_failure_keyboard(
                    task_id, verdict["evaluation_id"]
                ),
            )
            return
        try:
            grade = await grader.grade(
                database,
                learner,
                context=verdict["context"],
                answer=verdict["answer"],
            )
            verdict = await scheduler.finalize_tutor_evaluation(
                database,
                learner["id"],
                verdict["evaluation_id"],
                decision=grade.decision,
                feedback=grade.feedback_ru,
                model=grader.model,
                target_reproduced=grade.target_reproduced,
                task_valid=grade.task_valid,
                comparison_expression=grade.comparison_expression,
                corrected_answer=grade.corrected_answer,
            )
        except llm.BudgetExceeded as exc:
            await scheduler.fail_tutor_evaluation(
                database, learner["id"], verdict["evaluation_id"],
                error=str(exc), model=grader.model,
            )
            await status.edit_text(
                "Лимит проверки ответов исчерпан. Можно изменить ответ или перейти дальше без оценки.",
                reply_markup=grading_failure_keyboard(
                    task_id, verdict["evaluation_id"]
                ),
            )
            return
        except Exception as exc:
            logger.exception("answer grader call failed")
            await scheduler.fail_tutor_evaluation(
                database, learner["id"], verdict["evaluation_id"],
                error=str(exc), model=grader.model,
            )
            await status.edit_text(
                "Не удалось проверить ответ. Можно повторить проверку, изменить ответ "
                "или перейти дальше без оценки.",
                reply_markup=grading_failure_keyboard(
                    task_id, verdict["evaluation_id"]
                ),
            )
            return
        if verdict.get("stale"):
            await status.edit_text("Упражнение уже изменилось; результат проверки отброшен.")
            return
        await status.edit_text(
            verdict_text(verdict),
            parse_mode="HTML",
            reply_markup=post_answer_keyboard(
                task_id,
                youglish_query(verdict),
                can_compare=bool(verdict.get("can_compare")),
            ),
        )
        edit = False
    else:
        rendered = verdict_text(verdict)
        if edit:
            original = message.text or "Упражнение"
            await message.edit_text(
                f"{escape(original)}\n\n{rendered}",
                parse_mode="HTML",
                reply_markup=post_answer_keyboard(
                    task_id,
                    youglish_query(verdict),
                    can_compare=bool(verdict.get("can_compare")),
                ),
            )
        else:
            await message.answer(
                rendered,
                parse_mode="HTML",
                reply_markup=post_answer_keyboard(
                    task_id,
                    youglish_query(verdict),
                    can_compare=bool(verdict.get("can_compare")),
                ),
            )
    if verdict.get("session_complete"):
        await message.answer(_verdict_summary(verdict))
        return
    if verdict.get("can_compare"):
        return
    await send_task(
        message, database, learner, context_generator, previous_task_id=task_id
    )


def _verdict_summary(verdict: dict[str, Any]) -> str:
    return session_summary(
        {
            "answered_count": verdict.get("session_answered_count") or 0,
            "assessed_count": verdict.get("session_assessed_count") or 0,
            "correct_count": verdict.get("session_correct_count") or 0,
        }
    )


async def _skip_and_advance(
    callback: CallbackQuery,
    task_id: str,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService | None,
) -> None:
    verdict = await scheduler.skip_task(database, learner["id"], task_id)
    if verdict.get("error"):
        await callback.answer("Упражнение уже неактивно")
        return
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        verdict_text(verdict),
        parse_mode="HTML",
        reply_markup=post_answer_keyboard(task_id, youglish_query(verdict)),
    )
    if verdict.get("session_complete"):
        await callback.message.answer(_verdict_summary(verdict))
    else:
        await send_task(
            callback.message,
            database,
            learner,
            context_generator,
            previous_task_id=task_id,
        )


@router.callback_query(GradeCallback.filter())
async def resolve_grade_failure(
    callback: CallbackQuery,
    callback_data: GradeCallback,
    database: db.Database,
    learner: dict[str, Any],
    grader: TutorGraderService,
    context_generator: ContextExerciseService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if callback_data.action in {"wrong", "skip"}:
        await _skip_and_advance(
            callback, callback_data.task_id, database, learner, context_generator
        )
        return
    if callback_data.action != "retry":
        await callback.answer("Неизвестное действие")
        return
    evaluation = await scheduler.evaluation_for_retry(
        database, learner["id"], callback_data.evaluation_id
    )
    if not evaluation or evaluation["task_id"] != callback_data.task_id:
        await callback.answer("Проверка уже недоступна")
        return
    await callback.answer()
    await callback.message.edit_text("Повторяю проверку…", reply_markup=None)
    await finish_answer(
        callback.message,
        database,
        learner,
        callback_data.task_id,
        evaluation["answer"],
        edit=False,
        grader=grader,
        context_generator=context_generator,
    )


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(
        "Я помогу регулярно повторять слова.\n\n"
        "/practice — длинная тренировка\n"
        "/quick — быстрая тренировка на 5 минут\n"
        "/context — бесконечная практика в новых контекстах\n"
        "/contrast слово / другое слово — 3 упражнения на различия\n"
        "/decks — колоды\n"
        "/stats — статистика\n"
        "/howto — как работает обучение\n"
        "/reminders — умные напоминания\n"
        "/issues — карточки, требующие исправления\n"
        "/stop — остановить тренировку"
    )


async def _start_comparison(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    generator: ContextExerciseService | None,
    *,
    word_id: int,
    alternative: str,
    source_task_id: str | None = None,
    source_prompt: str = "",
) -> None:
    if generator is None or not generator.available:
        await message.answer("Для тренировки различий нужен настроенный преподаватель.")
        return
    session = await scheduler.start_session(
        database,
        learner["id"],
        kind="context",
        target_count=3,
        comparison={
            "word_id": word_id,
            "alternative": alternative,
            "source_task_id": source_task_id,
            "source_prompt": source_prompt,
        },
    )
    if session.get("already_active"):
        return
    await message.answer(
        "Начинаю отдельную тренировку различий: 3 контекста. Иногда подходят оба варианта. Интервалы слов не изменятся."
    )
    await send_task(message, database, learner, generator)


@router.message(Command("contrast"))
async def contrast_practice(
    message: Message,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService,
) -> None:
    pair = (message.text or "").partition(" ")[2].split(" / ", 1)
    if (
        len(pair) != 2
        or not all(part.strip() for part in pair)
        or len(pair[1].strip()) > 500
    ):
        await message.answer(
            "Укажите два слова или выражения: /contrast verfügbar / zugänglich. Первое слово должно быть в вашей колоде."
        )
        return
    word = await words.get_word(database, learner["id"], pair[0].strip())
    if word is None:
        await message.answer("Первое слово не найдено в ваших колодах.")
        return
    if word.lemma.casefold() == pair[1].strip().casefold():
        await message.answer("Укажите два разных слова или выражения.")
        return
    try:
        await _start_comparison(
            message,
            database,
            learner,
            context_generator,
            word_id=word.db_id,
            alternative=pair[1].strip(),
        )
    except ValueError:
        await message.answer("Для сравнения выберите активное слово из вашей колоды.")


@router.callback_query(FlowCallback.filter())
async def flow_action(
    callback: CallbackQuery,
    callback_data: FlowCallback,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    if callback_data.action == "skip":
        await _skip_and_advance(
            callback, callback_data.task_id, database, learner, context_generator
        )
        return
    task = await scheduler.task_context(database, learner["id"], callback_data.task_id)
    if not task or task["status"] != "answered":
        await callback.answer("Упражнение уже неактивно")
        return
    if callback_data.action in {"next", "continue"}:
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        await send_task(
            callback.message,
            database,
            learner,
            context_generator,
            previous_task_id=callback_data.task_id,
        )
    elif (
        callback_data.action == "compare"
        and task["verdict"].get("neutral_reason") == "alternative"
        and task["verdict"].get("comparison_expression")
        and task["answer_language"] == task["language"]
    ):
        await callback.answer()
        try:
            await _start_comparison(
                callback.message,
                database,
                learner,
                context_generator,
                word_id=task["word_id"],
                alternative=task["verdict"]["comparison_expression"],
                source_task_id=callback_data.task_id,
                source_prompt=task["payload"].get("prompt", ""),
            )
        except ValueError:
            await callback.message.answer(
                "Для сравнения выберите активное слово из вашей колоды."
            )
    else:
        await callback.answer("Действие недоступно")


@router.message(Command("practice"))
async def practice(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    await reminders.clear_pending_action(database, learner["id"])
    decks = [row for row in await words.list_decks(database, learner["id"]) if not row["is_archive"]]
    await message.answer(
        "Выберите колоду для тренировки:", reply_markup=deck_picker_keyboard(decks)
    )


@router.message(Command("quick"))
async def quick(message: Message, database: db.Database, learner: dict[str, Any]) -> None:
    await reminders.clear_pending_action(database, learner["id"])
    decks = [
        row
        for row in await words.list_decks(database, learner["id"])
        if not row["is_archive"]
    ]
    await message.answer(
        "Выберите колоду для быстрой тренировки:",
        reply_markup=deck_picker_keyboard(decks, quick=True),
    )


@router.message(Command("context"))
async def context_practice(
    message: Message, database: db.Database, learner: dict[str, Any]
) -> None:
    await reminders.clear_pending_action(database, learner["id"])
    decks = [
        row
        for row in await words.list_decks(database, learner["id"])
        if not row["is_archive"]
    ]
    await message.answer(
        "Выберите колоду для контекстной практики. "
        "Этот режим не изменяет стадии и интервалы:",
        reply_markup=deck_picker_keyboard(decks, context_mode=True),
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


@router.message(Command("howto", "help"))
async def howto(message: Message) -> None:
    for chunk in telegram_text_chunks(learning_guide_text()):
        await message.answer(chunk)


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


@router.callback_query(QuickDeckCallback.filter())
async def choose_quick_deck(
    callback: CallbackQuery,
    callback_data: QuickDeckCallback,
    database: db.Database,
    learner: dict[str, Any],
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    decks = [
        row
        for row in await words.list_decks(database, learner["id"])
        if not row["is_archive"]
    ]
    if callback_data.deck_id == -1:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=deck_picker_keyboard(
                decks, page=callback_data.page, quick=True
            )
        )
        return
    deck_id = callback_data.deck_id or None
    try:
        await scheduler.start_session(
            database, learner["id"], kind="quick", deck_id=deck_id
        )
    except (LookupError, ValueError):
        await callback.answer("Колода больше недоступна")
        return
    await callback.answer()
    await callback.message.edit_text(
        "Быстрая тренировка началась и продлится 5 минут. /stop — закончить раньше.",
        reply_markup=None,
    )
    await send_task(callback.message, database, learner)


@router.callback_query(ContextDeckCallback.filter())
async def choose_context_deck(
    callback: CallbackQuery,
    callback_data: ContextDeckCallback,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    decks = [
        row
        for row in await words.list_decks(database, learner["id"])
        if not row["is_archive"]
    ]
    if callback_data.deck_id == -1:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=deck_picker_keyboard(
                decks, page=callback_data.page, context_mode=True
            )
        )
        return
    deck_id = callback_data.deck_id or None
    try:
        await scheduler.start_session(
            database, learner["id"], kind="context", deck_id=deck_id
        )
    except (LookupError, ValueError):
        await callback.answer("Колода больше недоступна")
        return
    await callback.answer()
    await callback.message.edit_text(
        "Контекстная практика началась. Прогресс карточек не изменяется; "
        "/stop — закончить.",
        reply_markup=None,
    )
    await send_task(callback.message, database, learner, context_generator)


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
    grader: TutorGraderService | None = None,
    context_generator: ContextExerciseService | None = None,
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
        grader=grader,
        context_generator=context_generator,
    )


@router.callback_query(WordActionCallback.filter())
async def word_action(
    callback: CallbackQuery,
    callback_data: WordActionCallback,
    database: db.Database,
    learner: dict[str, Any],
    context_generator: ContextExerciseService,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    context = await scheduler.task_context(database, learner["id"], callback_data.task_id)
    if not context or context["status"] != "answered":
        await callback.answer("Упражнение уже неактивно")
        return
    if callback_data.action == "cancel":
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=post_answer_keyboard(
                callback_data.task_id, youglish_query(context)
            )
        )
        return
    if callback_data.action == "flag":
        result = await words.flag_answered_task_word(
            database,
            learner["id"],
            callback_data.task_id,
            reason="Ожидается комментарий пользователя",
        )
        if not result:
            await callback.answer("Карточка больше недоступна")
            return
        await reminders.set_pending_action(
            database,
            learner["id"],
            "card_feedback",
            {
                "task_id": callback_data.task_id,
                "word_id": context["word_id"],
            },
        )
        await callback.answer()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Что нужно исправить в карточке «{context['word']}»? "
            "Опишите ошибку одним сообщением."
        )
        return
    if callback_data.action != "archive":
        await callback.answer("Неизвестное действие")
        return
    if not callback_data.confirm:
        await callback.answer()
        await callback.message.edit_reply_markup(
            reply_markup=confirm_word_action_keyboard(
                callback_data.task_id, callback_data.action
            )
        )
        return
    result = await words.archive_answered_task_word(
        database, learner["id"], callback_data.task_id
    )
    message = "Карточка перенесена в архив."
    if not result:
        await callback.answer("Упражнение уже неактивно")
        return
    await callback.answer()
    original = callback.message.text or "Упражнение"
    await callback.message.edit_text(f"{original}\n\n{message}", reply_markup=None)
    if (
        not await scheduler.get_open_task(database, learner["id"])
        and await scheduler.get_open_session(database, learner["id"])
    ):
        await send_task(callback.message, database, learner, context_generator)
