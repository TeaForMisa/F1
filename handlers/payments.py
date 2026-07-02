"""
Оплата Pro-доступа через Telegram Stars (валюта XTR).

Весь биллинг — это входящие апдейты:
  • pro:buy            → выставляем счёт (sendInvoice; по умолчанию разовый
                          платёж на 30 дней — PRO_SUBSCRIPTION включает рекуррентную
                          подписку, если она доступна боту);
  • pre_checkout_query → подтверждаем (ответить нужно за ~10 сек, без тяжёлой логики);
  • successful_payment → двигаем premium_until в БД. Идемпотентность — по charge_id.

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
    # Рекуррентная подписка — только если явно включена (иначе Telegram отдаёт
    # SUBSCRIPTION_EXPORT_MISSING на ботах без включённых подписок). По умолчанию —
    # разовый платёж на 30 дней (продление повторной покупкой).
    subscription = _SUBSCRIPTION_PERIOD if config.pro_subscription else None
    await message.answer_invoice(
        title="⭐ F1 Bot Pro — поддержать разработку",
        description=(
            "Результаты сессий, избранный пилот и доп. напоминания на 30 дней. "
            "Без автопродления — звёзды спишутся только один раз."
        ),
        payload="pro_monthly",
        currency="XTR",
        prices=[LabeledPrice(label="F1 Bot Pro — 30 дней, без автопродления", amount=config.pro_price_stars)],
        subscription_period=subscription,
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
    user = await db.upsert_user(u.id, u.username, u.full_name)

    # Идемпотентность: при drop_pending_updates=False апдейт может прийти повторно.
    is_new = await db.record_payment(charge_id, u.id, sp.total_amount)

    exp = sp.subscription_expiration_date
    if exp is not None:
        # Подписка: Telegram сам посчитал дату окончания.
        until_iso = exp.astimezone(timezone.utc).isoformat()
    else:
        # Разовый платёж: +30 дней от текущего срока (если ещё активен) или от сейчас.
        base = datetime.now(timezone.utc)
        if user and user.premium_until:
            try:
                current = datetime.fromisoformat(user.premium_until)
                if current > base:
                    base = current
            except ValueError:
                pass
        until_iso = (base + timedelta(seconds=_SUBSCRIPTION_PERIOD)).isoformat()

    await db.set_premium(u.id, until_iso, charge_id)

    if is_new:
        await message.answer(texts.PRO_PAID, parse_mode="HTML")
    log.info(
        "Оплата Pro: user=%s amount=%s★ new=%s until=%s",
        u.id, sp.total_amount, is_new, until_iso,
    )
