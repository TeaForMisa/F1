"""
Асинхронный клиент открытого Jolpica-F1 API (форк Ergast, бесплатный, без ключей).
Базовый URL: https://api.jolpi.ca/ergast/f1/
"""
from __future__ import annotations

import asyncio
import logging
import time as _time
from datetime import datetime, timezone
from typing import Any

import httpx

from config import config
from db import replace_sessions

log = logging.getLogger(__name__)

API_BASE = "https://api.jolpi.ca/ergast/f1"
TIMEOUT = 20.0

COUNTRY_FLAGS = {
    "Australia": "🇦🇺", "China": "🇨🇳", "Japan": "🇯🇵", "Bahrain": "🇧🇭",
    "Saudi Arabia": "🇸🇦", "USA": "🇺🇸", "Miami": "🇺🇸", "Monaco": "🇲🇨",
    "Spain": "🇪🇸", "Canada": "🇨🇦", "UK": "🇬🇧", "Great Britain": "🇬🇧",
    "Austria": "🇦🇹", "Belgium": "🇧🇪", "Hungary": "🇭🇺", "Netherlands": "🇳🇱",
    "Italy": "🇮🇹", "Azerbaijan": "🇦🇿", "Singapore": "🇸🇬", "Mexico": "🇲🇽",
    "Brazil": "🇧🇷", "Qatar": "🇶🇦", "UAE": "🇦🇪", "Argentina": "🇦🇷",
}

# Ergast/Jolpica отдают национальность пилота/команды отдельным полем в формате
# прилагательного ("British", "Dutch" и т.д.) — это НЕ страна проведения этапа,
# поэтому нужна отдельная таблица соответствия.
NATIONALITY_FLAGS = {
    "British": "🇬🇧", "Dutch": "🇳🇱", "Monegasque": "🇲🇨", "Spanish": "🇪🇸",
    "Mexican": "🇲🇽", "Finnish": "🇫🇮", "French": "🇫🇷", "Australian": "🇦🇺",
    "Canadian": "🇨🇦", "German": "🇩🇪", "Thai": "🇹🇭", "Japanese": "🇯🇵",
    "Danish": "🇩🇰", "American": "🇺🇸", "Chinese": "🇨🇳", "Italian": "🇮🇹",
    "Brazilian": "🇧🇷", "Argentine": "🇦🇷", "Argentinian": "🇦🇷",
    "New Zealander": "🇳🇿", "Belgian": "🇧🇪", "Austrian": "🇦🇹",
    "Swedish": "🇸🇪", "Polish": "🇵🇱", "Russian": "🇷🇺", "Indian": "🇮🇳",
    "Indonesian": "🇮🇩", "Swiss": "🇨🇭", "Portuguese": "🇵🇹",
    "Hungarian": "🇭🇺", "South African": "🇿🇦", "Venezuelan": "🇻🇪",
    "Colombian": "🇨🇴", "Irish": "🇮🇪", "Uruguayan": "🇺🇾",
    "Malaysian": "🇲🇾", "Emirati": "🇦🇪",
}

SESSION_TYPES = [
    ("practice1", "Свободная практика 1", "FirstPractice"),
    ("practice2", "Свободная практика 2", "SecondPractice"),
    ("practice3", "Свободная практика 3", "ThirdPractice"),
    ("sprint_qualifying", "Спринт-квалификация", "SprintQualifying"),
    ("sprint", "Спринт", "Sprint"),
    ("qualifying", "Квалификация", "Qualifying"),
    ("race", "Гонка", None),
]


def _flag(country: str) -> str:
    return COUNTRY_FLAGS.get(country, "🏁")


def nationality_flag(nationality: str | None) -> str:
    """Флаг страны пилота/команды по полю nationality из Ergast/Jolpica API."""
    return NATIONALITY_FLAGS.get((nationality or "").strip(), "🏳️")


def _parse_dt(date_str: str, time_str: str | None) -> datetime:
    t = (time_str or "12:00:00Z").replace("Z", "")
    dt = datetime.fromisoformat(f"{date_str}T{t}")
    return dt.replace(tzinfo=timezone.utc)


async def _fetch_season_races(client: httpx.AsyncClient) -> list[dict]:
    url = f"{API_BASE}/{config.f1_season}.json"
    for attempt in range(1, 4):
        try:
            r = await client.get(url, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            return data["MRData"]["RaceTable"].get("Races", [])
        except Exception as e:
            log.warning("Попытка %d получить календарь: %s", attempt, e)
            if attempt == 3:
                raise
            await asyncio.sleep(1.5 * attempt)
    return []


async def refresh_sessions_cache() -> int:
    async with httpx.AsyncClient(headers={"User-Agent": "F1TelegramBot/1.0"}) as client:
        races = await _fetch_season_races(client)

    sessions: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    for race in races:
        round_no = int(race["round"])
        race_name = race["raceName"]
        circuit = race["Circuit"]["circuitName"]
        loc = race["Circuit"]["Location"]
        city = loc["locality"]
        country = loc["country"]
        flag = _flag(country)

        for sess_type, sess_name, json_key in SESSION_TYPES:
            if json_key is None:
                if "date" not in race:
                    continue
                start = _parse_dt(race["date"], race.get("time"))
            else:
                sess = race.get(json_key)
                if not sess or "date" not in sess:
                    continue
                start = _parse_dt(sess["date"], sess.get("time"))

            sessions.append({
                "session_key": f"{config.f1_season}_{round_no}_{sess_type}",
                "season": config.f1_season,
                "round": round_no,
                "race_name": race_name,
                "session_type": sess_type,
                "session_name": sess_name,
                "start_time_utc": start.isoformat(),
                "circuit": circuit,
                "city": city,
                "country": country,
                "flag_emoji": flag,
                "cached_at": now_iso,
            })

    await replace_sessions(sessions)
    log.info("Кеш сессий обновлён: %d записей", len(sessions))
    return len(sessions)


_cache: dict[str, tuple[float, list[dict]]] = {}


async def _fetch_standings(endpoint: str, list_key: str, cache_key: str) -> list[dict]:
    now = _time.monotonic()
    cached = _cache.get(cache_key)
    if cached:
        ts, data = cached
        if now - ts < config.standings_cache_ttl:
            return data

    async with httpx.AsyncClient(headers={"User-Agent": "F1TelegramBot/1.0"}) as client:
        r = await client.get(
            f"{API_BASE}/{config.f1_season}/{endpoint}.json", timeout=TIMEOUT
        )
        r.raise_for_status()
        data = r.json()
        standings = data["MRData"]["StandingsTable"]["StandingsLists"]
        if not standings:
            result: list[dict] = []
        else:
            result = standings[0].get(list_key, [])

    _cache[cache_key] = (now, result)
    return result


async def fetch_driver_standings() -> list[dict]:
    return await _fetch_standings("driverStandings", "DriverStandings", "drivers")


async def fetch_constructor_standings() -> list[dict]:
    return await _fetch_standings("constructorStandings", "ConstructorStandings", "constructors")


def invalidate_standings_cache() -> None:
    _cache.clear()
