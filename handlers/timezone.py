"""Команда /timezone и обработчики выбора часового пояса."""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import texts
from config import config
from keyboards import timezone_picker, timezone_picker_back
from security import is_valid_tz

log = logging.getLogger(__name__)
router = Router(name="timezone")


class TzState(StatesGroup):
    waiting_custom = State()


@router.message(Command("timezone"))
async def cmd_timezone(message: Message) -> None:
    await message.answer(
        texts.TZ_PICKER_TITLE,
        reply_markup=timezone_picker(),
    )


@router.callback_query(F.data == "tz:back")
async def cb_tz_back(callback: CallbackQuery) -> None:
    await callback.message.edit_text(
        texts.TZ_PICKER_TITLE,
        reply_markup=timezone_picker(),
    )
    await callback.answer()


@router.callback_query(F.data == "tz:custom")
async def cb_tz_custom(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        texts.TZ_CUSTOM_PROMPT,
        parse_mode="HTML",
        reply_markup=timezone_picker_back(),
    )
    await state.set_state(TzState.waiting_custom)
    await callback.answer()


@router.message(TzState.waiting_custom, F.text)
async def on_custom_tz(message: Message, state: FSMContext) -> None:
    tz_input = (message.text or "").strip()

    if not is_valid_tz(tz_input):
        await message.answer(
            texts.TZ_INVALID,
            parse_mode="HTML",
            reply_markup=timezone_picker_back(),
        )
        return

    await db.set_timezone(message.from_user.id, tz_input)
    await state.clear()
    await message.answer(texts.TZ_SET_OK.format(tz=tz_input), parse_mode="HTML")


@router.callback_query(F.data.startswith("tz:"))
async def cb_tz_select(callback: CallbackQuery, state: FSMContext) -> None:
    tz = callback.data.split(":", 1)[1] if ":" in callback.data else ""
    if tz in ("custom", "back"):
        return

    if not is_valid_tz(tz):
        log.warning(
            "Невалидный tz в callback от user %s: %r",
            callback.from_user.id, callback.data,
        )
        await callback.answer("❌ Неверный часовой пояс", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    await db.set_timezone(callback.from_user.id, tz)
    await state.clear()

    if user and user.timezone != config.default_timezone:
        await callback.message.edit_text(
            texts.TZ_SET_OK.format(tz=tz),
            parse_mode="HTML",
        )
    else:
        await callback.message.edit_text(
            texts.WELCOME_DONE.format(tz=tz),
            parse_mode="HTML",
        )
    await callback.answer()
