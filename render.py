"""
Рендер информационных экранов в готовый HTML-текст.

Вынесено отдельно, чтобы один и тот же вывод использовали и текстовые команды
(/next, /schedule, /standings, /constructors), и inline-меню (handlers/menu.py) —
без дублирования логики.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import db
import f1_api
import texts
from config import config

log = logging.getLogger(__name__)


async def render_next(tz: str) -> str:
    now = datetime.now(timezone.utc)
    session = await db.get_next_session(now)
    if not session:
        return texts.NO_NEXT_SESSION.format(season=config.f1_season)
    start = datetime.fromisoformat(session["start_time_utc"])
    return texts.next_session_text(
        session_type=session["session_type"],
        flag=session["flag_emoji"],
        race_name=session["race_name"],
        session_name=session["session_name"],
        when_local=texts.fmt_dt_local(start, tz),
        circuit=session["circuit"],
        city=session["city"],
        countdown=texts.fmt_countdown(start, now),
    )


async def render_schedule(tz: str) -> str:
    now = datetime.now(timezone.utc)
    sessions = await db.get_next_race_weekend(now)
    if not sessions:
        return texts.NO_NEXT_SESSION.format(season=config.f1_season)
    first = sessions[0]
    lines = [texts.schedule_header(
        flag=first["flag_emoji"],
        race_name=first["race_name"],
        circuit=first["circuit"],
        city=first["city"],
    )]
    for s in sessions:
        start = datetime.fromisoformat(s["start_time_utc"])
        lines.append(texts.schedule_item(
            session_type=s["session_type"],
            session_name=s["session_name"],
            when_local=texts.fmt_dt_local(start, tz),
        ))
    return "".join(lines)


async def render_standings() -> str:
    try:
        standings = await f1_api.fetch_driver_standings()
    except Exception as e:
        log.warning("Ошибка получения зачёта пилотов: %s", e)
        return texts.ERROR_GENERIC
    rows = []
    for s in standings:
        d = s["Driver"]
        team = s["Constructors"][0]["name"] if s.get("Constructors") else "—"
        name = f"{d.get('givenName', '')} {d.get('familyName', '')}".strip()
        rows.append((
            s.get("position", "?"),
            f1_api.nationality_flag(d.get("nationality")),
            name,
            team,
            s.get("points", "0"),
        ))
    return texts.standings_table(config.f1_season, rows)


async def render_constructors() -> str:
    try:
        standings = await f1_api.fetch_constructor_standings()
    except Exception as e:
        log.warning("Ошибка получения зачёта конструкторов: %s", e)
        return texts.ERROR_GENERIC
    rows = []
    for s in standings:
        c = s.get("Constructor", {})
        rows.append((
            s.get("position", "?"),
            f1_api.nationality_flag(c.get("nationality")),
            c.get("name", "—"),
            s.get("points", "0"),
        ))
    return texts.constructors_table(config.f1_season, rows)
