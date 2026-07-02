"""Информационные команды: /next, /schedule, /standings, /constructors."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import db
import f1_api
import texts
from config import config

log = logging.getLogger(__name__)
router = Router(name="info")


def _user_tz_or_default(user) -> str:
    return user.timezone if user else config.default_timezone


@router.message(Command("next"))
async def cmd_next(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    tz = _user_tz_or_default(user)
    now = datetime.now(timezone.utc)

    session = await db.get_next_session(now)
    if not session:
        await message.answer(texts.NO_NEXT_SESSION.format(season=config.f1_season))
        return

    start = datetime.fromisoformat(session["start_time_utc"])
    text = texts.next_session_text(
        session_type=session["session_type"],
        flag=session["flag_emoji"],
        race_name=session["race_name"],
        session_name=session["session_name"],
        when_local=texts.fmt_dt_local(start, tz),
        circuit=session["circuit"],
        city=session["city"],
        countdown=texts.fmt_countdown(start, now),
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("schedule"))
async def cmd_schedule(message: Message) -> None:
    user = await db.get_user(message.from_user.id)
    tz = _user_tz_or_default(user)
    now = datetime.now(timezone.utc)

    sessions = await db.get_next_race_weekend(now)
    if not sessions:
        await message.answer(texts.NO_NEXT_SESSION.format(season=config.f1_season))
        return

    first = sessions[0]
    lines = [texts.schedule_header(
        flag=first["flag_emoji"],
        race_name=first["race_name"],
        circuit=first["circuit"],
        city=first["city"],
    )]
    for s in sessions:
        start = datetime.fromisoformat(s["start_time_utc"])
        lines.append(
            texts.schedule_item(
                session_type=s["session_type"],
                session_name=s["session_name"],
                when_local=texts.fmt_dt_local(start, tz),
            )
        )
    await message.answer("".join(lines), parse_mode="HTML")


@router.message(Command("standings"))
async def cmd_standings(message: Message) -> None:
    try:
        standings = await f1_api.fetch_driver_standings()
    except Exception as e:
        log.warning("Ошибка получения зачёта пилотов: %s", e)
        await message.answer(texts.ERROR_GENERIC)
        return

    if not standings:
        await message.answer(texts.STANDINGS_EMPTY)
        return

    lines = [texts.STANDINGS_HEADER.format(season=config.f1_season)]
    for s in standings:
        d = s["Driver"]
        team = s["Constructors"][0]["name"] if s.get("Constructors") else "—"
        name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
        lines.append(
            texts.standings_row(
                pos=s.get("position", "?"),
                flag=f1_api.nationality_flag(d.get("nationality")),
                name=name,
                team=team,
                pts=s.get("points", "0"),
            )
        )
    await message.answer("".join(lines), parse_mode="HTML")


@router.message(Command("constructors"))
async def cmd_constructors(message: Message) -> None:
    try:
        standings = await f1_api.fetch_constructor_standings()
    except Exception as e:
        log.warning("Ошибка получения зачёта конструкторов: %s", e)
        await message.answer(texts.ERROR_GENERIC)
        return

    if not standings:
        await message.answer(texts.CONSTRUCTORS_EMPTY)
        return

    lines = [texts.CONSTRUCTORS_HEADER.format(season=config.f1_season)]
    for s in standings:
        c = s.get("Constructor", {})
        lines.append(
            texts.constructors_row(
                pos=s.get("position", "?"),
                flag=f1_api.nationality_flag(c.get("nationality")),
                name=c.get("name", "—"),
                pts=s.get("points", "0"),
            )
        )
    await message.answer("".join(lines), parse_mode="HTML")
