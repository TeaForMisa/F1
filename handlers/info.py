"""Информационные команды: /next, /schedule, /standings, /constructors."""
from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import db
import render
from config import config

router = Router(name="info")


def _user_tz_or_default(user) -> str:
    return user.timezone if user else config.default_timezone


@router.message(Command("next"))
async def cmd_next(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    await message.answer(await render.render_next(_user_tz_or_default(user)), parse_mode="HTML")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    await message.answer(await render.render_schedule(_user_tz_or_default(user)), parse_mode="HTML")


@router.message(Command("standings"))
async def cmd_standings(message: Message) -> None:
    await message.answer(await render.render_standings(), parse_mode="HTML")


@router.message(Command("constructors"))
async def cmd_constructors(message: Message) -> None:
    await message.answer(await render.render_constructors(), parse_mode="HTML")
