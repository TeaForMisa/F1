"""Базовые команды: /start, /help."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import db
import texts
from config import config
from keyboards import timezone_picker

router = Router(name="common")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = message.from_user
    if not user:
        return
    await db.upsert_user(user.id, user.username, user.full_name)
    await db.unpause_user(user.id)

    await message.answer(
        texts.WELCOME.format(season=config.f1_season),
        parse_mode="HTML",
        reply_markup=timezone_picker(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        texts.HELP.format(season=config.f1_season),
        parse_mode="HTML",
    )
