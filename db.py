"""
Асинхронная работа с SQLite.

Таблицы:
  users              — настройки пользователя (часовой пояс, флаги уведомлений)
  sent_notifications — лог отправленных уведомлений (защита от дублей при рестартах)
  sessions_cache     — кешированное расписание сессий Ф1 (обновляется раз в сутки)
  fsm_storage        — состояния диалогов aiogram (переживают рестарт/пересборку)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator, Optional

import aiosqlite

from config import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id        INTEGER PRIMARY KEY,
    username       TEXT,
    full_name      TEXT,
    timezone       TEXT    NOT NULL DEFAULT 'Europe/Moscow',
    notify_2h      INTEGER NOT NULL DEFAULT 1,
    notify_1h      INTEGER NOT NULL DEFAULT 1,
    notify_30min   INTEGER NOT NULL DEFAULT 1,
    paused         INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT    NOT NULL,
    updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sent_notifications (
    user_id        INTEGER NOT NULL,
    session_key    TEXT    NOT NULL,
    lead_minutes   INTEGER NOT NULL,
    sent_at        TEXT    NOT NULL,
    PRIMARY KEY (user_id, session_key, lead_minutes)
);

CREATE TABLE IF NOT EXISTS sessions_cache (
    session_key    TEXT PRIMARY KEY,
    season         INTEGER NOT NULL,
    round          INTEGER NOT NULL,
    race_name      TEXT    NOT NULL,
    session_type   TEXT    NOT NULL,
    session_name   TEXT    NOT NULL,
    start_time_utc TEXT    NOT NULL,
    circuit        TEXT,
    city           TEXT,
    country        TEXT,
    flag_emoji     TEXT,
    cached_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fsm_storage (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions_cache(start_time_utc);
CREATE INDEX IF NOT EXISTS idx_sent_lookup ON sent_notifications(session_key, lead_minutes);
CREATE INDEX IF NOT EXISTS idx_sent_sent_at ON sent_notifications(sent_at);
CREATE INDEX IF NOT EXISTS idx_users_paused ON users(paused);
"""


class User:
    def __init__(self, row: aiosqlite.Row):
        self.user_id: int = row["user_id"]
        self.username: Optional[str] = row["username"]
        self.full_name: Optional[str] = row["full_name"]
        self.timezone: str = row["timezone"]
        self.notify_2h: bool = bool(row["notify_2h"])
        self.notify_1h: bool = bool(row["notify_1h"])
        self.notify_30min: bool = bool(row["notify_30min"])
        self.paused: bool = bool(row["paused"])
        self.created_at: Optional[str] = row["created_at"]


_db_path: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def init_db() -> None:
    global _db_path
    _db_path = config.db_path
    parent = Path(_db_path).parent
    if str(parent) and not parent.exists():
        try:
            parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    async with aiosqlite.connect(_db_path) as db:
        # WAL сохраняется в самом файле БД и переживает переподключения — читатели
        # (например /stats) не блокируют писателя (рассылку/планировщик).
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(SCHEMA)
        await db.commit()


@asynccontextmanager
async def _connect() -> AsyncIterator[aiosqlite.Connection]:
    if _db_path is None:
        raise RuntimeError("init_db() не вызван")
    db = await aiosqlite.connect(_db_path)
    try:
        # Не падать с "database is locked" при параллельных операциях
        # (рассылка + планировщик уведомлений пишут одновременно).
        await db.execute("PRAGMA busy_timeout = 5000")
        yield db
    finally:
        await db.close()


async def upsert_user(user_id: int, username: Optional[str], full_name: Optional[str]) -> User:
    username = (username or "")[:64] or None
    full_name = (full_name or "")[:128] or None
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO users (user_id, username, full_name, timezone, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name,
                updated_at = excluded.updated_at
            """,
            (user_id, username, full_name, config.default_timezone, _now_iso(), _now_iso()),
        )
        await db.commit()
    return await get_user(user_id)  # type: ignore[return-value]


async def get_user(user_id: int) -> Optional[User]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return User(row) if row else None


async def set_timezone(user_id: int, tz: str) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET timezone = ?, updated_at = ? WHERE user_id = ?",
            (tz, _now_iso(), user_id),
        )
        await db.commit()


_LEAD_COLUMNS = {"2h": "notify_2h", "1h": "notify_1h", "30min": "notify_30min"}


async def toggle_notify(user_id: int, lead: str) -> None:
    column = _LEAD_COLUMNS.get(lead)
    if column is None:
        return
    async with _connect() as db:
        await db.execute(
            f"UPDATE users SET {column} = 1 - {column}, updated_at = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        await db.commit()


async def pause_user(user_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET paused = 1, updated_at = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        await db.commit()


async def unpause_user(user_id: int) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET paused = 0, updated_at = ? WHERE user_id = ?",
            (_now_iso(), user_id),
        )
        await db.commit()


async def is_sent(user_id: int, session_key: str, lead_minutes: int) -> bool:
    async with _connect() as db:
        cur = await db.execute(
            "SELECT 1 FROM sent_notifications WHERE user_id=? AND session_key=? AND lead_minutes=?",
            (user_id, session_key, lead_minutes),
        )
        return await cur.fetchone() is not None


async def mark_sent(user_id: int, session_key: str, lead_minutes: int) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO sent_notifications (user_id, session_key, lead_minutes, sent_at) VALUES (?, ?, ?, ?)",
            (user_id, session_key, lead_minutes, _now_iso()),
        )
        await db.commit()


_LEAD_COLUMNS_BY_MIN = {120: "notify_2h", 60: "notify_1h", 30: "notify_30min"}


async def get_users_for_notification(session_key: str, lead_minutes: int) -> list[User]:
    column = _LEAD_COLUMNS_BY_MIN.get(lead_minutes)
    if column is None:
        return []
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            f"""
            SELECT u.* FROM users u
            LEFT JOIN sent_notifications s
              ON s.user_id = u.user_id
             AND s.session_key = ?
             AND s.lead_minutes = ?
            WHERE u.{column} = 1
              AND u.paused = 0
              AND s.user_id IS NULL
            """,
            (session_key, lead_minutes),
        )
        rows = await cur.fetchall()
        return [User(r) for r in rows]


async def replace_sessions(sessions: list[dict]) -> None:
    async with _connect() as db:
        await db.execute("DELETE FROM sessions_cache")
        await db.executemany(
            """
            INSERT INTO sessions_cache
              (session_key, season, round, race_name, session_type, session_name,
               start_time_utc, circuit, city, country, flag_emoji, cached_at)
            VALUES (:session_key, :season, :round, :race_name, :session_type, :session_name,
                    :start_time_utc, :circuit, :city, :country, :flag_emoji, :cached_at)
            """,
            sessions,
        )
        await db.commit()


async def get_upcoming_sessions(now: datetime, limit: int = 20) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM sessions_cache
            WHERE start_time_utc > ?
            ORDER BY start_time_utc ASC
            LIMIT ?
            """,
            (now.isoformat(), limit),
        )
        return await cur.fetchall()


async def get_next_session(now: datetime) -> Optional[aiosqlite.Row]:
    rows = await get_upcoming_sessions(now, limit=1)
    return rows[0] if rows else None


async def get_next_race_weekend(now: datetime) -> list[aiosqlite.Row]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT round FROM sessions_cache
            WHERE start_time_utc > ?
            GROUP BY round ORDER BY MIN(start_time_utc) ASC LIMIT 1
            """,
            (now.isoformat(),),
        )
        row = await cur.fetchone()
        if not row:
            return []
        cur = await db.execute(
            """
            SELECT * FROM sessions_cache
            WHERE round = ? AND start_time_utc > ?
            ORDER BY start_time_utc ASC
            """,
            (row["round"], now.isoformat()),
        )
        return await cur.fetchall()


async def get_sessions_for_notifications(now: datetime) -> list[aiosqlite.Row]:
    now_iso = now.isoformat()
    horizon_iso = (now + timedelta(hours=2, minutes=1)).isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM sessions_cache
            WHERE start_time_utc > ? AND start_time_utc <= ?
            ORDER BY start_time_utc ASC
            """,
            (now_iso, horizon_iso),
        )
        return await cur.fetchall()


async def cleanup_old_notifications(days: int = 7) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _connect() as db:
        cur = await db.execute(
            "DELETE FROM sent_notifications WHERE sent_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount or 0


async def get_stats() -> dict:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT COUNT(*) as c FROM users")
        total_users = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) as c FROM users WHERE paused = 0")
        active_users = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) as c FROM users WHERE paused = 1")
        paused_users = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) as c FROM sent_notifications")
        total_sent = (await cur.fetchone())["c"]
        day_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        cur = await db.execute(
            "SELECT COUNT(*) as c FROM sent_notifications WHERE sent_at >= ?",
            (day_ago,),
        )
        sent_24h = (await cur.fetchone())["c"]
        cur = await db.execute("SELECT COUNT(*) as c FROM sessions_cache")
        sessions_cached = (await cur.fetchone())["c"]
        cur = await db.execute(
            "SELECT COUNT(*) as c FROM users WHERE created_at >= ?",
            (day_ago,),
        )
        new_users_24h = (await cur.fetchone())["c"]
        return {
            "total_users": total_users,
            "active_users": active_users,
            "paused_users": paused_users,
            "total_sent": total_sent,
            "sent_24h": sent_24h,
            "sessions_cached": sessions_cached,
            "new_users_24h": new_users_24h,
        }


async def get_recent_users(limit: int = 20) -> list[User]:
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [User(r) for r in await cur.fetchall()]


async def get_all_active_user_ids() -> list[int]:
    async with _connect() as db:
        cur = await db.execute("SELECT user_id FROM users WHERE paused = 0")
        rows = await cur.fetchall()
        return [r[0] for r in rows]


# --- FSM-хранилище (см. fsm_storage.SQLiteStorage) ---


async def fsm_set_state(key: str, state: Optional[str]) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO fsm_storage (key, state, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET state = excluded.state, updated_at = excluded.updated_at
            """,
            (key, state, _now_iso()),
        )
        # Пустые записи (нет ни состояния, ни данных) не нужны — подчищаем.
        await db.execute(
            "DELETE FROM fsm_storage WHERE state IS NULL AND data = '{}'"
        )
        await db.commit()


async def fsm_get_state(key: str) -> Optional[str]:
    async with _connect() as db:
        cur = await db.execute("SELECT state FROM fsm_storage WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def fsm_set_data(key: str, data_json: str) -> None:
    async with _connect() as db:
        await db.execute(
            """
            INSERT INTO fsm_storage (key, data, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET data = excluded.data, updated_at = excluded.updated_at
            """,
            (key, data_json, _now_iso()),
        )
        await db.execute(
            "DELETE FROM fsm_storage WHERE state IS NULL AND data = '{}'"
        )
        await db.commit()


async def fsm_get_data(key: str) -> str:
    async with _connect() as db:
        cur = await db.execute("SELECT data FROM fsm_storage WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else "{}"


async def cleanup_stale_fsm(days: int = 2) -> int:
    """Удалить брошенные состояния диалогов (пользователь начал ввод и пропал)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _connect() as db:
        cur = await db.execute(
            "DELETE FROM fsm_storage WHERE updated_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount or 0
