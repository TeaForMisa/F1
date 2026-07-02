"""
Точка входа F1 Telegram-бота.

Запуск:
    cd f1-bot
    cp .env.example .env
    # вставить BOT_TOKEN от @BotFather, ADMIN_IDS=свой_telegram_id
    pip install -r requirements.txt
    python main.py
"""
from __future__ import annotations

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

import handlers
from config import config
from db import init_db
from f1_api import refresh_sessions_cache
from scheduler import init_scheduler
from security import ThrottlingMiddleware

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("apscheduler").setLevel(logging.WARNING)
log = logging.getLogger("f1bot")


async def on_startup(bot: Bot) -> None:
    log.info("Инициализация БД (путь: %s)…", config.db_path)
    await init_db()

    log.info("Загрузка расписания сезона %d…", config.f1_season)
    try:
        count = await refresh_sessions_cache()
        log.info("Загружено %d сессий", count)
    except Exception as e:
        log.error("Не удалось загрузить расписание: %s. Бот стартует, повторим позже.", e)

    log.info("Запуск планировщика уведомлений…")
    init_scheduler(bot)

    await bot.delete_webhook(drop_pending_updates=True)

    if config.admin_ids:
        log.info("Администраторы: %s", ", ".join(str(a) for a in config.admin_ids))
    else:
        log.warning("ADMIN_IDS не задан — админ-команды недоступны.")

    log.info("Бот запущен и готов к работе ✅")


async def main() -> None:
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(ThrottlingMiddleware(throttle_seconds=config.throttle_seconds))
    dp.callback_query.middleware(ThrottlingMiddleware(throttle_seconds=config.throttle_seconds))

    handlers.register(dp)

    await on_startup(bot)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        try:
            from scheduler import _scheduler_ref
            if _scheduler_ref:
                _scheduler_ref.shutdown(wait=False)
        except Exception:
            pass
        await bot.session.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Завершение по сигналу")
        sys.exit(0)
