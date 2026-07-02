"""Конфигурация бота. Значения берутся из переменных окружения / .env файла."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")


def _parse_admin_ids(raw: str) -> tuple[int, ...]:
    """Распарсить ADMIN_IDS='123,456,789' → (123, 456, 789)."""
    ids: list[int] = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            continue
    return tuple(ids)


@dataclass(frozen=True)
class Config:
    bot_token: str
    default_timezone: str
    db_path: str
    f1_season: int
    scheduler_interval: int
    admin_ids: tuple[int, ...]
    throttle_seconds: int
    standings_cache_ttl: int
    max_tz_input_length: int

    @classmethod
    def load(cls) -> "Config":
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise RuntimeError(
                "BOT_TOKEN не задан. Скопируйте .env.example в .env "
                "и вставьте токен, полученный у @BotFather."
            )

        # DB_PATH: на Bothost по умолчанию /app/data/f1bot.db (персистентная папка).
        db_path = os.getenv("DB_PATH", "").strip()
        if not db_path:
            bothost_data = Path("/app/data")
            if bothost_data.exists() and bothost_data.is_dir():
                db_path = str(bothost_data / "f1bot.db")
            else:
                db_path = "f1bot.db"

        return cls(
            bot_token=token,
            default_timezone=os.getenv("DEFAULT_TIMEZONE", "Europe/Moscow").strip(),
            db_path=db_path,
            f1_season=int(os.getenv("F1_SEASON", "2026")),
            scheduler_interval=int(os.getenv("SCHEDULER_INTERVAL", "30")),
            admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS", "")),
            throttle_seconds=int(os.getenv("THROTTLE_SECONDS", "3")),
            standings_cache_ttl=int(os.getenv("STANDINGS_CACHE_TTL", "300")),
            max_tz_input_length=int(os.getenv("MAX_TZ_INPUT_LENGTH", "50")),
        )

    def is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_ids


config = Config.load()
