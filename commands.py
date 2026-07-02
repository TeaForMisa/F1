"""
Список команд для кнопки «Меню» в Telegram (BotCommand-меню), чтобы пользователь
мог открывать разделы кнопкой, а не вводом текстовых команд вручную.
"""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import BotCommand, BotCommandScopeChat, BotCommandScopeDefault

from config import config

log = logging.getLogger(__name__)

PUBLIC_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="🏠 Старт / приветствие"),
    BotCommand(command="menu", description="📋 Меню с кнопками"),
    BotCommand(command="next", description="🏎 Ближайшая сессия"),
    BotCommand(command="schedule", description="🗓 Расписание уик-энда"),
    BotCommand(command="standings", description="🏆 Личный зачёт"),
    BotCommand(command="constructors", description="🏭 Кубок конструкторов"),
    BotCommand(command="timezone", description="🌍 Часовой пояс"),
    BotCommand(command="settings", description="⚙️ Уведомления"),
    BotCommand(command="help", description="ℹ️ Помощь"),
    BotCommand(command="cancel", description="❌ Отменить текущее действие"),
]

ADMIN_COMMANDS: list[BotCommand] = PUBLIC_COMMANDS + [
    BotCommand(command="admin", description="🔧 Админ-панель"),
    BotCommand(command="stats", description="📊 Статистика"),
    BotCommand(command="users", description="👥 Пользователи"),
    BotCommand(command="broadcast", description="📢 Рассылка"),
    BotCommand(command="refresh", description="🔄 Обновить кеш"),
]


async def setup_commands(bot: Bot) -> None:
    """Задать кнопку «Меню»: у всех — публичные команды, у админов — ещё и админские."""
    await bot.set_my_commands(PUBLIC_COMMANDS, scope=BotCommandScopeDefault())
    for admin_id in config.admin_ids:
        try:
            await bot.set_my_commands(ADMIN_COMMANDS, scope=BotCommandScopeChat(chat_id=admin_id))
        except Exception as e:
            log.warning("Не удалось задать меню команд для админа %s: %s", admin_id, e)
