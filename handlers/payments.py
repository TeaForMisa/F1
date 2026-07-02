"""
Оплата Pro-подписки через Telegram Stars (валюта XTR).

Весь биллинг — это входящие апдейты:
  • pro:buy            → выставляем счёт-подписку (sendInvoice, subscription_period);
  • pre_checkout_query → подтверждаем (ответить нужно за ~10 сек, без тяжёлой логики);
  • successful_payment → и первичная оплата, и каждое автопродление: двигаем
                          premium_until в БД. Идемпотентность — по charge_id.

Инлайн-экраны Pro (витрина/управление/избранный пилот) живут в handlers/menu.py.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

import db
import texts
from config import config

log = logging.getLogger(__name__)
router = Router(name="payments")

# 30 дней — единственный допустимый период подписки для Telegram Stars.
_SUBSCRIPTION_PERIOD = 2592000


async def _send_invoice(message: Message) -> None:
    await message.answer_invoice(
        title="F1 Bot Pro",
        description=(
            "Результаты сессий, избранный пилот и гибкие напоминания. "
            "Автопродление раз в месяц, отмена в любой момент."
        ),
        payload="pro_monthly",
        currency="XTR",
        prices=[LabeledPrice(label="F1 Bot Pro — 1 месяц", amount=config.pro_price_stars)],
        subscription_period=_SUBSCRIPTION_PERIOD,
        provider_token="",  # для Stars токен провайдера не нужен
    )


@router.callback_query(F.data == "pro:buy")
async def cb_buy(callback: CallbackQuery) -> None:
    if callback.message is not None:
        await _send_invoice(callback.message)
    await callback.answer()


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery) -> None:
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_paid(message: Message) -> None:
    sp = message.successful_payment
    charge_id = sp.telegram_payment_charge_id
    u = message.from_user

    # Гарантируем, что строка пользователя существует (вдруг оплатил до /start).
    await db.upsert_user(u.id, u.username, u.full_name)

    # Идемпотентность: при drop_pending_updates=False апдейт может прийти повторно.
    is_new = await db.record_payment(charge_id, u.id, sp.total_amount)

    exp = sp.subscription_expiration_date
    if exp is not None:
        until_iso = exp.astimezone(timezone.utc).isoformat()
    else:
        until_iso = (datetime.now(timezone.utc) + timedelta(seconds=_SUBSCRIPTION_PERIOD)).isoformat()

    await db.set_premium(message.from_user.id, until_iso, charge_id)

    if is_new:
        await message.answer(texts.PRO_PAID, parse_mode="HTML")
    log.info(
        "Оплата Pro: user=%s amount=%s★ new=%s until=%s",
        message.from_user.id, sp.total_amount, is_new, until_iso,
    )
