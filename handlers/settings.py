"""Команда /settings и переключатели уведомлений."""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import db
import texts
from keyboards import settings_kb

log = logging.getLogger(__name__)
router = Router(name="settings")

_VALID_LEADS = {"2h", "1h", "30min"}
_ATTR_MAP = {"2h": "notify_2h", "1h": "notify_1h", "30min": "notify_30min"}
_LABELS = {"2h": "За 2 часа", "1h": "За 1 час", "30min": "За 30 минут"}


@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    if not user:
        await message.answer("Нажми /start, чтобы начать.")
        return
    await message.answer(
        texts.SETTINGS_HEADER.format(tz=user.timezone),
        parse_mode="HTML",
        reply_markup=settings_kb(user),
    )


@router.callback_query(F.data.startswith("toggle:"))
async def cb_toggle(callback: CallbackQuery) -> None:
    lead = callback.data.split(":", 1)[1] if ":" in callback.data else ""

    if lead not in _VALID_LEADS:
        log.warning(
            "Невалидный toggle от user %s: %r",
            callback.from_user.id, callback.data,
        )
        await callback.answer("❌ Неверная кнопка", show_alert=True)
        return

    await db.toggle_notify(callback.from_user.id, lead)
    user = await db.get_user(callback.from_user.id)

    is_on = bool(user and getattr(user, _ATTR_MAP[lead]))
    state = "включено ✅" if is_on else "выключено ❌"
    await callback.answer(f"{_LABELS[lead]}: {state}", show_alert=False)

    await callback.message.edit_reply_markup(reply_markup=settings_kb(user))
