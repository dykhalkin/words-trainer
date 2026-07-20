"""Bot process composition and lifecycle."""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from vocab import db, scheduler

from .config import Settings, load_settings
from .agent import TutorService
from .chat import router as chat_router
from .curator import CuratorService, run_curator_cycle
from .drill import router
from .logging import configure_logging
from .middleware import LearnerMiddleware
from .push import run_push_cycle

logger = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    database = db.Database(settings.database_url)
    bot = Bot(settings.telegram_bot_token.get_secret_value())
    dispatcher = Dispatcher()
    dispatcher.update.outer_middleware(
        LearnerMiddleware(database, settings.allowed_chat_ids)
    )
    dispatcher.include_router(router)
    dispatcher.include_router(chat_router)
    tutor = TutorService(settings)
    curator = CuratorService(settings)
    jobs = AsyncIOScheduler(job_defaults={"coalesce": True, "misfire_grace_time": 900})
    jobs.add_job(
        run_push_cycle,
        "interval",
        seconds=settings.push_check_seconds,
        args=(bot, database),
        max_instances=1,
    )
    jobs.add_job(scheduler.sweep_expired, "interval", minutes=10, args=(database,), max_instances=1)
    jobs.add_job(
        scheduler.close_idle_sessions, "interval", minutes=5, args=(database,), max_instances=1
    )
    jobs.add_job(
        run_curator_cycle,
        "interval",
        hours=1,
        args=(curator, bot, database),
        max_instances=1,
    )
    try:
        await database.open()
        await bot.set_my_commands(
            [
                BotCommand(command="practice", description="длинная тренировка"),
                BotCommand(command="decks", description="мои колоды"),
                BotCommand(command="stats", description="статистика"),
                BotCommand(command="stop", description="остановить тренировку"),
            ]
        )
        jobs.start()
        await dispatcher.start_polling(bot, close_bot_session=False, tutor=tutor)
    finally:
        if jobs.running:
            jobs.shutdown(wait=False)
        await database.close()
        await bot.session.close()


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_path)
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        logger.info("bot stopped")
