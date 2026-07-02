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

import f1_api
import handlers
from commands import setup_commands
from config import config
from db import init_db
from fsm_storage import SQLiteStorage
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


async def on_startup(bot: Bot):
    log.info("Загрузка расписания сезона %d…", config.f1_season)
    try:
        count = await f1_api.refresh_sessions_cache()
        log.info("Загружено %d сессий", count)
    except Exception as e:
        log.error("Не удалось загрузить расписание: %s. Бот стартует, повторим позже.", e)

    log.info("Запуск планировщика уведомлений…")
    scheduler = init_scheduler(bot)

    await setup_commands(bot)

    # Не сбрасываем накопившиеся апдейты: на Bothost пересборка занимает
    # несколько минут, и команды, отправленные в это время, должны обработаться
    # после рестарта, а не пропасть. От флуда защищает ThrottlingMiddleware.
    await bot.delete_webhook(drop_pending_updates=False)

    if config.admin_ids:
        log.info("Администраторы: %s", ", ".join(str(a) for a in config.admin_ids))
    else:
        log.warning("ADMIN_IDS не задан — админ-команды недоступны.")

    log.info("Бот запущен и готов к работе ✅")
    return scheduler


async def main() -> None:
    # БД инициализируется до диспетчера: SQLiteStorage (FSM) пишет в ту же базу.
    log.info("Инициализация БД (путь: %s)…", config.db_path)
    await init_db()

    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    dp = Dispatcher(storage=SQLiteStorage())

    dp.message.middleware(ThrottlingMiddleware(throttle_seconds=config.throttle_seconds))
    # Нажатия инлайн-кнопок троттлим слабее: навигация по меню должна быть
    # отзывчивой (полный throttle_seconds отсекал бы клики по меню как спам).
    dp.callback_query.middleware(ThrottlingMiddleware(throttle_seconds=config.callback_throttle_seconds))

    handlers.register(dp)

    scheduler = await on_startup(bot)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            pass
        await f1_api.close_client()
        await bot.session.close()
        log.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Завершение по сигналу")
        sys.exit(0)
