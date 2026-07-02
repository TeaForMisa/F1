"""Базовые команды: /start, /help."""
from __future__ import annotations

import logging
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, Message

import db
import texts
from config import config
from keyboards import timezone_picker

log = logging.getLogger(__name__)
router = Router(name="common")

# Фото для приветственного сообщения. Если файла нет — бот пришлёт обычный текст.
WELCOME_PHOTO = Path(__file__).resolve().parent.parent / "assets" / "welcome.jpg"


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    await db.upsert_user(user.id, user.username, user.full_name)
    await db.unpause_user(user.id)

    caption = texts.welcome_text(name=user.full_name, season=config.f1_season)

    if WELCOME_PHOTO.exists():
        try:
            await message.answer_photo(
                FSInputFile(WELCOME_PHOTO),
                caption=caption,
                parse_mode="HTML",
                reply_markup=timezone_picker(),
            )
            return
        except Exception as e:
            log.warning("Не удалось отправить приветственное фото: %s", e)

    await message.answer(caption, parse_mode="HTML", reply_markup=timezone_picker())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        texts.HELP.format(season=config.f1_season),
        parse_mode="HTML",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    """Отменить текущее действие (ввод часового пояса, рассылку и т.п.)."""
    if await state.get_state() is None:
        await message.answer("Нечего отменять — активных действий нет.")
        return
    await state.clear()
    await message.answer("❌ Действие отменено.")
