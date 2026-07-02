"""
Асинхронная работа с SQLite.

Таблицы:
  users              — настройки пользователя (часовой пояс, флаги уведомлений, Pro)
  sent_notifications — лог отправленных уведомлений (защита от дублей при рестартах)
  sessions_cache     — кешированное расписание сессий Ф1 (обновляется раз в сутки)
  fsm_storage        — состояния диалогов aiogram (переживают рестарт/пересборку)
  processed_payments — обработанные платежи Stars (идемпотентность автосписаний)
  results_sent       — по каким сессиям результаты уже разосланы (Pro)
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
    notify_1d      INTEGER NOT NULL DEFAULT 0,
    notify_10min   INTEGER NOT NULL DEFAULT 0,
    notify_results INTEGER NOT NULL DEFAULT 0,
    favorite_driver      TEXT,
    favorite_driver_name TEXT,
    premium_until  TEXT,
    sub_charge_id  TEXT,
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
    circuit_id     TEXT,
    wiki_url       TEXT,
    cached_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS fsm_storage (
    key        TEXT PRIMARY KEY,
    state      TEXT,
    data       TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_payments (
    charge_id  TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    amount     INTEGER NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS results_sent (
    session_key TEXT PRIMARY KEY,
    sent_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_start ON sessions_cache(start_time_utc);
CREATE INDEX IF NOT EXISTS idx_sent_lookup ON sent_notifications(session_key, lead_minutes);
CREATE INDEX IF NOT EXISTS idx_sent_sent_at ON sent_notifications(sent_at);
CREATE INDEX IF NOT EXISTS idx_users_paused ON users(paused);
"""

# Индексы по колонкам, которые появляются только после миграции (_MIGRATIONS).
# Их нельзя держать в SCHEMA: на старой базе колонки ещё нет в момент executescript.
_POST_MIGRATION_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_users_premium ON users(premium_until)",
)

# Колонки, добавляемые в существующие таблицы (миграция старых баз на Bothost).
_MIGRATIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "sessions_cache": (("circuit_id", "TEXT"), ("wiki_url", "TEXT")),
    "users": (
        ("notify_1d", "INTEGER NOT NULL DEFAULT 0"),
        ("notify_10min", "INTEGER NOT NULL DEFAULT 0"),
        ("notify_results", "INTEGER NOT NULL DEFAULT 0"),
        ("favorite_driver", "TEXT"),
        ("favorite_driver_name", "TEXT"),
        ("premium_until", "TEXT"),
        ("sub_charge_id", "TEXT"),
    ),
}


def _rget(row: aiosqlite.Row, key: str, default=None):
    """Безопасный доступ к колонке (для полей, которые могут отсутствовать в выборке)."""
    return row[key] if key in row.keys() else default


class User:
    def __init__(self, row: aiosqlite.Row):
        self.user_id: int = row["user_id"]
        self.username: Optional[str] = row["username"]
        self.full_name: Optional[str] = row["full_name"]
        self.timezone: str = row["timezone"]
        self.notify_2h: bool = bool(row["notify_2h"])
        self.notify_1h: bool = bool(row["notify_1h"])
        self.notify_30min: bool = bool(row["notify_30min"])
        self.notify_1d: bool = bool(_rget(row, "notify_1d", 0))
        self.notify_10min: bool = bool(_rget(row, "notify_10min", 0))
        self.notify_results: bool = bool(_rget(row, "notify_results", 0))
        self.favorite_driver: Optional[str] = _rget(row, "favorite_driver")
        self.favorite_driver_name: Optional[str] = _rget(row, "favorite_driver_name")
        self.premium_until: Optional[str] = _rget(row, "premium_until")
        self.paused: bool = bool(row["paused"])
        self.created_at: Optional[str] = row["created_at"]

    @property
    def is_premium(self) -> bool:
        return bool(self.premium_until) and self.premium_until > _now_iso()


_db_path: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_premium(user: Optional["User"]) -> bool:
    return bool(user) and user.is_premium


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
        # Миграция существующих баз: CREATE TABLE IF NOT EXISTS не добавляет новые
        # колонки в уже созданную таблицу, поэтому добавляем их вручную.
        for table, columns in _MIGRATIONS.items():
            for column, ddl in columns:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                except Exception:
                    pass  # колонка уже существует
        # Индексы по новым колонкам — только после того, как колонки гарантированно есть.
        for index_sql in _POST_MIGRATION_INDEXES:
            try:
                await db.execute(index_sql)
            except Exception:
                pass
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


_LEAD_COLUMNS = {
    "2h": "notify_2h", "1h": "notify_1h", "30min": "notify_30min",
    "1d": "notify_1d", "10min": "notify_10min", "results": "notify_results",
}


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


_LEAD_COLUMNS_BY_MIN = {
    1440: "notify_1d", 120: "notify_2h", 60: "notify_1h", 30: "notify_30min", 10: "notify_10min",
}
# Лиды, доступные только по Pro-подписке.
_PREMIUM_LEADS = {1440, 10}


async def get_users_for_notification(session_key: str, lead_minutes: int) -> list[User]:
    column = _LEAD_COLUMNS_BY_MIN.get(lead_minutes)
    if column is None:
        return []
    premium_only = lead_minutes in _PREMIUM_LEADS
    params: list = [session_key, lead_minutes]
    premium_clause = ""
    if premium_only:
        premium_clause = "AND u.premium_until IS NOT NULL AND u.premium_until > ?"
        params.append(_now_iso())
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
              {premium_clause}
            """,
            params,
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
               start_time_utc, circuit, city, country, flag_emoji, circuit_id, wiki_url, cached_at)
            VALUES (:session_key, :season, :round, :race_name, :session_type, :session_name,
                    :start_time_utc, :circuit, :city, :country, :flag_emoji, :circuit_id, :wiki_url, :cached_at)
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


async def get_all_rounds() -> list[aiosqlite.Row]:
    """Один ряд на этап сезона (для календаря): метаданные + границы времени."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT round, race_name, country, flag_emoji, circuit, city, circuit_id,
                   MIN(start_time_utc) AS first_start,
                   MAX(start_time_utc) AS last_start
            FROM sessions_cache
            GROUP BY round
            ORDER BY round ASC
            """
        )
        return await cur.fetchall()


async def get_round(round_no: int) -> list[aiosqlite.Row]:
    """Все сессии одного этапа, по возрастанию времени."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM sessions_cache WHERE round = ? ORDER BY start_time_utc ASC",
            (round_no,),
        )
        return await cur.fetchall()


async def get_sessions_for_notifications(now: datetime) -> list[aiosqlite.Row]:
    now_iso = now.isoformat()
    # 24 ч + 1 мин — чтобы ловить и премиум-лид «за 1 день» (1440 мин).
    horizon_iso = (now + timedelta(hours=24, minutes=1)).isoformat()
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


# --- Pro-подписка (Telegram Stars) ---


async def set_premium(user_id: int, until_iso: str, charge_id: Optional[str]) -> None:
    """Записать/продлить премиум до until_iso (ISO UTC). Вызывается на каждую оплату."""
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET premium_until = ?, sub_charge_id = ?, updated_at = ? WHERE user_id = ?",
            (until_iso, charge_id, _now_iso(), user_id),
        )
        await db.commit()


async def record_payment(charge_id: str, user_id: int, amount: int) -> bool:
    """Зафиксировать платёж. True — новый, False — уже обрабатывали (защита от дублей)."""
    async with _connect() as db:
        cur = await db.execute(
            "INSERT OR IGNORE INTO processed_payments (charge_id, user_id, amount, created_at) VALUES (?, ?, ?, ?)",
            (charge_id, user_id, amount, _now_iso()),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_sub_charge_id(user_id: int) -> Optional[str]:
    async with _connect() as db:
        cur = await db.execute("SELECT sub_charge_id FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


async def set_favorite_driver(user_id: int, driver_id: Optional[str], name: Optional[str]) -> None:
    async with _connect() as db:
        await db.execute(
            "UPDATE users SET favorite_driver = ?, favorite_driver_name = ?, updated_at = ? WHERE user_id = ?",
            (driver_id, name, _now_iso(), user_id),
        )
        await db.commit()


async def get_premium_stats() -> dict:
    async with _connect() as db:
        cur = await db.execute(
            "SELECT COUNT(*) AS c FROM users WHERE premium_until IS NOT NULL AND premium_until > ?",
            (_now_iso(),),
        )
        active = (await cur.fetchone())[0]
        cur = await db.execute("SELECT COUNT(*) AS c FROM processed_payments")
        payments = (await cur.fetchone())[0]
        return {"premium_active": active, "payments_total": payments}


# --- Результаты сессий (Pro) ---


async def get_users_for_results() -> list[User]:
    """Активные премиум-подписчики с включённым пушем результатов."""
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT * FROM users
            WHERE notify_results = 1 AND paused = 0
              AND premium_until IS NOT NULL AND premium_until > ?
            """,
            (_now_iso(),),
        )
        return [User(r) for r in await cur.fetchall()]


async def get_sessions_awaiting_results(now: datetime, hours: int = 6) -> list[aiosqlite.Row]:
    """Недавно завершившиеся гонки/квалы/спринты, по которым результаты ещё не разосланы."""
    lo = (now - timedelta(hours=hours)).isoformat()
    hi = now.isoformat()
    async with _connect() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """
            SELECT c.* FROM sessions_cache c
            LEFT JOIN results_sent r ON r.session_key = c.session_key
            WHERE c.start_time_utc <= ? AND c.start_time_utc >= ?
              AND c.session_type IN ('race', 'qualifying', 'sprint')
              AND r.session_key IS NULL
            ORDER BY c.start_time_utc ASC
            """,
            (hi, lo),
        )
        return await cur.fetchall()


async def mark_results_sent(session_key: str) -> None:
    async with _connect() as db:
        await db.execute(
            "INSERT OR IGNORE INTO results_sent (session_key, sent_at) VALUES (?, ?)",
            (session_key, _now_iso()),
        )
        await db.commit()


async def cleanup_old_results_marks(days: int = 14) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    async with _connect() as db:
        cur = await db.execute("DELETE FROM results_sent WHERE sent_at < ?", (cutoff,))
        await db.commit()
        return cur.rowcount or 0
