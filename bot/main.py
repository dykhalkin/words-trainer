"""Bot process composition and lifecycle."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from vocab import db, jobs as job_store

from .config import Settings, load_settings
from .agent import TutorService
from .chat import router as chat_router
from .curator import CuratorService
from .drill import router
from .grader import TutorGraderService
from .logging import configure_logging
from .middleware import LearnerMiddleware
from .reminders import router as reminders_router
from .jobs import BotJobRunner

logger = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    database = db.Database(settings.database_url)
    bot = Bot(settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(
        LearnerMiddleware(database, settings.allowed_chat_ids)
    )
    dispatcher.include_router(router)
    dispatcher.include_router(reminders_router)
    dispatcher.include_router(chat_router)
    tutor = TutorService(settings)
    grader = TutorGraderService(settings)
    curator = CuratorService(settings)
    job_runner = BotJobRunner(database, bot, curator)
    scheduler = AsyncIOScheduler(job_defaults={"coalesce": True, "misfire_grace_time": 900})
    scheduler.add_job(
        job_runner.scheduled,
        "interval",
        seconds=settings.push_check_seconds,
        args=("push", settings.push_check_seconds),
        max_instances=1,
    )
    scheduler.add_job(
        job_runner.scheduled, "interval", minutes=10, args=("task_sweep", 600), max_instances=1
    )
    scheduler.add_job(
        job_runner.scheduled,
        "interval",
        minutes=5,
        args=("session_cleanup", 300),
        max_instances=1,
    )
    scheduler.add_job(
        job_runner.scheduled,
        "interval",
        minutes=5,
        args=("curator_plan", 300),
        max_instances=1,
    )
    scheduler.add_job(
        job_runner.scheduled,
        "interval",
        seconds=30,
        args=("reminder_refresh", 30),
        max_instances=1,
    )
    scheduler.add_job(
        job_runner.scheduled,
        "interval",
        hours=1,
        args=("weekly_digest", 3600),
        max_instances=1,
    )
    scheduler.add_job(job_runner.drain_manual, "interval", seconds=10, max_instances=1)
    try:
        await database.open()
        await job_store.ensure_controls(database)
        await job_store.recover_interrupted(database)
        await bot.set_my_commands(
            [
                BotCommand(command="practice", description="длинная тренировка"),
                BotCommand(command="decks", description="мои колоды"),
                BotCommand(command="stats", description="статистика"),
                BotCommand(command="issues", description="карточки с ошибками"),
                BotCommand(command="reminders", description="умные напоминания"),
                BotCommand(command="stop", description="остановить тренировку"),
            ]
        )
        scheduler.start()
        await dispatcher.start_polling(
            bot, close_bot_session=False, tutor=tutor, grader=grader
        )
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        await database.close()
        await bot.session.close()


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_path)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("bot stopped")
