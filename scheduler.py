"""
Планировщик уведомлений на базе APScheduler.

Логика:
  • Каждые 30 секунд планировщик смотрит все сессии, которые начнутся в ближайшие 2 часа.
  • Для каждой сессии и каждого lead (2ч / 1ч / 30мин) проверяет:
      - наступило ли время отправки (now >= session_start - lead)
      - сессия ещё не началась (now < session_start)
    Если оба условия — рассылает уведомление всем пользователям, у которых
    включён соответствующий флаг и которые ещё не получили это уведомление.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

import db
import texts
from config import config
from f1_api import refresh_sessions_cache

log = logging.getLogger(__name__)

LEADS = [120, 60, 30]

_scheduler_ref = None


async def _send_notification(bot: Bot, user: db.User, session, lead: int) -> None:
    start = datetime.fromisoformat(session["start_time_utc"])
    text = texts.notify_text(
        session_type=session["session_type"],
        flag=session["flag_emoji"],
        race_name=session["race_name"],
        session_name=session["session_name"],
        lead_text=texts.LEAD_TEXTS[lead],
        when_local=texts.fmt_dt_local(start, user.timezone),
        circuit=session["circuit"],
        city=session["city"],
    )
    try:
        await bot.send_message(user.user_id, text, parse_mode="HTML")
    except TelegramRetryAfter as e:
        log.warning("Telegram flood control, ждём %s сек", e.retry_after)
        await asyncio.sleep(e.retry_after)
        try:
            await bot.send_message(user.user_id, text, parse_mode="HTML")
        except Exception as e2:
            log.error("Повторная ошибка отправки user %s: %s", user.user_id, e2)
    except TelegramForbiddenError:
        log.info("Пользователь %s заблокировал бота — отключаем уведомления", user.user_id)
        await db.pause_user(user.user_id)
    except Exception as e:
        log.error("Ошибка отправки уведомления пользователю %s: %s", user.user_id, e)


async def _check_and_notify(bot: Bot) -> None:
    now = datetime.now(timezone.utc)
    try:
        sessions = await db.get_sessions_for_notifications(now)
    except Exception as e:
        log.error("Ошибка получения сессий из БД: %s", e)
        return

    if not sessions:
        return

    total_sent = 0
    for session in sessions:
        start = datetime.fromisoformat(session["start_time_utc"])
        if now >= start:
            continue

        for lead in LEADS:
            notify_at = start - timedelta(minutes=lead)
            if now < notify_at:
                continue

            session_key = session["session_key"]
            try:
                users = await db.get_users_for_notification(session_key, lead)
            except Exception as e:
                log.error("Ошибка получения пользователей: %s", e)
                continue

            for user in users:
                await _send_notification(bot, user, session, lead)
                await db.mark_sent(user.user_id, session_key, lead)
                total_sent += 1
                # Небольшая пауза, чтобы не упереться в общий лимит Telegram
                # (~30 сообщений/сек) при массовой рассылке перед стартом сессии.
                await asyncio.sleep(0.04)

    if total_sent:
        log.info("Отправлено уведомлений: %d", total_sent)


async def _daily_refresh() -> None:
    log.info("Запуск ежедневного обновления кеша сессий")
    try:
        await refresh_sessions_cache()
    except Exception as e:
        log.error("Ошибка обновления кеша сессий: %s", e)


async def _daily_cleanup() -> None:
    try:
        deleted = await db.cleanup_old_notifications(days=7)
        if deleted:
            log.info("Очищено старых записей уведомлений: %d", deleted)
    except Exception as e:
        log.error("Ошибка очистки старых уведомлений: %s", e)


def init_scheduler(bot: Bot) -> AsyncIOScheduler:
    global _scheduler_ref
    scheduler = AsyncIOScheduler(timezone=timezone.utc)

    scheduler.add_job(
        _check_and_notify,
        trigger=IntervalTrigger(seconds=config.scheduler_interval),
        args=[bot],
        id="notify_check",
        max_instances=1,
        coalesce=True,
        next_run_time=datetime.now(timezone.utc),
    )

    scheduler.add_job(
        _daily_refresh,
        trigger=CronTrigger(hour=4, minute=0, timezone=timezone.utc),
        id="daily_refresh",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _daily_cleanup,
        trigger=CronTrigger(hour=3, minute=0, timezone=timezone.utc),
        id="daily_cleanup",
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()
    _scheduler_ref = scheduler
    log.info("Планировщик запущен (интервал проверки: %d сек)", config.scheduler_interval)
    return scheduler
