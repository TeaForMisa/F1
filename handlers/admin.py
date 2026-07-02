"""
Админ-команды: /stats, /users, /user, /broadcast, /refresh, /admin.

Доступ только для user_id из config.admin_ids (переменная ADMIN_IDS в .env).
Не-админам команды не отвечают (чтобы не раскрывать наличие админки).
"""
from __future__ import annotations

import asyncio
import logging
from functools import wraps

from aiogram import Router, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

import db
import f1_api
import texts
from config import config
from security import esc, sanitize_int

log = logging.getLogger(__name__)
router = Router(name="admin")


class BroadcastState(StatesGroup):
    waiting_text = State()


def _admin_only(handler):
    """Декоратор: пропускает обработку если пользователь не админ."""
    @wraps(handler)
    async def wrapper(message: Message, *args, **kwargs):
        if not message.from_user or not config.is_admin(message.from_user.id):
            return
        return await handler(message, *args, **kwargs)
    return wrapper


@router.message(Command("admin"))
@_admin_only
async def cmd_admin(message: Message) -> None:
    await message.answer(texts.ADMIN_HELP, parse_mode="HTML")


@router.message(Command("stats"))
@_admin_only
async def cmd_stats(message: Message) -> None:
    stats = await db.get_stats()
    premium = await db.get_premium_stats()
    text = texts.ADMIN_STATS.format(
        total_users=stats["total_users"],
        active_users=stats["active_users"],
        paused_users=stats["paused_users"],
        new_users_24h=stats["new_users_24h"],
        total_sent=stats["total_sent"],
        sent_24h=stats["sent_24h"],
        sessions_cached=stats["sessions_cached"],
        premium_active=premium["premium_active"],
        payments_total=premium["payments_total"],
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("users"))
@_admin_only
async def cmd_users(message: Message) -> None:
    users = await db.get_recent_users(limit=20)
    if not users:
        await message.answer(texts.ADMIN_NO_USERS)
        return
    lines = [texts.ADMIN_USERS_HEADER.format(count=len(users))]
    for u in users:
        lines.append(
            texts.ADMIN_USER_ROW.format(
                id=u.user_id,
                username=esc(u.username or "—"),
                full_name=esc(u.full_name or "—"),
                tz=esc(u.timezone),
                paused="да" if u.paused else "нет",
            )
        )
    await message.answer("".join(lines), parse_mode="HTML")


@router.message(Command("user"))
@_admin_only
async def cmd_user(message: Message) -> None:
    parts = (message.text or "").split(None, 1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/user &lt;id&gt;</code>", parse_mode="HTML")
        return
    user_id = sanitize_int(parts[1].strip(), default=0)
    if user_id <= 0:
        await message.answer("Неверный ID. Использование: <code>/user 123456789</code>", parse_mode="HTML")
        return

    user = await db.get_user(user_id)
    if not user:
        await message.answer(texts.ADMIN_USER_NOT_FOUND)
        return

    text = texts.ADMIN_USER_DETAIL.format(
        user_id=esc(user.user_id),
        username=esc(user.username or "—"),
        full_name=esc(user.full_name or "—"),
        tz=esc(user.timezone),
        created=esc(user.created_at or "—"),
        n2h="✅" if user.notify_2h else "❌",
        n1h="✅" if user.notify_1h else "❌",
        n30m="✅" if user.notify_30min else "❌",
        paused="да" if user.paused else "нет",
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("broadcast"))
@_admin_only
async def cmd_broadcast(message: Message, state: FSMContext) -> None:
    await state.set_state(BroadcastState.waiting_text)
    await message.answer(texts.ADMIN_BROADCAST_PROMPT, parse_mode="HTML")


@router.message(BroadcastState.waiting_text, F.text, ~F.text.startswith("/"))
@_admin_only
async def on_broadcast_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    broadcast_text = message.text or ""

    if len(broadcast_text) > 4000:
        await message.answer("❌ Текст слишком длинный (макс. 4000 символов).")
        return

    user_ids = await db.get_all_active_user_ids()
    if not user_ids:
        await message.answer("Активных пользователей нет.")
        return

    await message.answer(f"📢 Начинаю рассылку {len(user_ids)} пользователям…")

    sent = 0
    errors = 0
    for uid in user_ids:
        try:
            await message.bot.send_message(uid, broadcast_text, parse_mode="HTML")
            sent += 1
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            try:
                await message.bot.send_message(uid, broadcast_text, parse_mode="HTML")
                sent += 1
            except Exception:
                errors += 1
        except TelegramForbiddenError:
            await db.pause_user(uid)
            errors += 1
        except Exception as e:
            log.warning("Ошибка рассылки user %s: %s", uid, e)
            errors += 1
        await asyncio.sleep(0.04)

    await message.answer(
        texts.ADMIN_BROADCAST_RESULT.format(
            sent=sent,
            errors=errors,
            total=len(user_ids),
        ),
        parse_mode="HTML",
    )


@router.message(Command("refresh"))
@_admin_only
async def cmd_refresh(message: Message) -> None:
    await message.answer("🔄 Обновляю кеш сессий…")
    try:
        count = await f1_api.refresh_sessions_cache()
        f1_api.invalidate_standings_cache()
        await message.answer(texts.ADMIN_REFRESH_DONE.format(count=count), parse_mode="HTML")
    except Exception as e:
        log.error("Ошибка ручного обновления кеша: %s", e)
        await message.answer(texts.ADMIN_REFRESH_FAILED)


@router.message(Command("refund"))
@_admin_only
async def cmd_refund(message: Message) -> None:
    parts = (message.text or "").split(None, 1)
    user_id = sanitize_int(parts[1].strip(), default=0) if len(parts) > 1 else 0
    if user_id <= 0:
        await message.answer("Использование: <code>/refund &lt;user_id&gt;</code>", parse_mode="HTML")
        return
    charge_id = await db.get_sub_charge_id(user_id)
    if not charge_id:
        await message.answer("У пользователя нет платежа Stars для возврата.")
        return
    try:
        await message.bot.refund_star_payment(user_id, charge_id)
        await message.answer(f"✅ Возврат Stars пользователю {user_id} выполнен.")
    except Exception as e:
        log.error("Ошибка возврата Stars user %s: %s", user_id, e)
        await message.answer(f"❌ Не удалось вернуть Stars: {esc(str(e))}", parse_mode="HTML")
