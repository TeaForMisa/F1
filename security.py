"""
Модуль безопасности:
  • esc()              — экранирование HTML (защита от инъекций в данных из API)
  • ThrottlingMiddleware — ограничение частоты команд от одного пользователя
  • is_valid_tz()      — валидация часового пояса
  • sanitize_int()     — безопасный парсинг целых чисел из ненадёжного ввода
"""
from __future__ import annotations

import html
import logging
import re
import time
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TgUser
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import config

log = logging.getLogger(__name__)

_TZ_PATTERN = re.compile(r"^[A-Za-z0-9_+\-/]+$")


def esc(value: Any) -> str:
    """Экранировать значение для безопасной вставки в HTML-сообщение Telegram."""
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def is_valid_tz(tz: str) -> bool:
    """Проверить, что строка — корректный IANA часовой пояс."""
    if not tz or len(tz) > config.max_tz_input_length:
        return False
    if not _TZ_PATTERN.match(tz):
        return False
    try:
        ZoneInfo(tz)
        return True
    except ZoneInfoNotFoundError:
        return False


def sanitize_int(value: Any, default: int = 0) -> int:
    """Безопасно привести значение к int. При ошибке вернуть default."""
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return default


class ThrottlingMiddleware(BaseMiddleware):
    """Ограничивает частоту обработки апдейтов от одного пользователя."""

    _CLEANUP_EVERY = 1000  # звонков между чистками, чтобы _last_call не рос бесконечно

    def __init__(self, throttle_seconds: float = 3.0):
        self.throttle_seconds = throttle_seconds
        self._last_call: dict[int, float] = {}
        self._calls_since_cleanup = 0

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: TgUser | None = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        now = time.monotonic()
        last = self._last_call.get(user.id, 0.0)
        if now - last < self.throttle_seconds:
            log.debug("Троттлинг: пользователь %s превысил лимит частоты", user.id)
            return None

        self._last_call[user.id] = now
        self._calls_since_cleanup += 1
        if self._calls_since_cleanup >= self._CLEANUP_EVERY:
            self._calls_since_cleanup = 0
            cutoff = now - self.throttle_seconds * 10
            stale = [uid for uid, ts in self._last_call.items() if ts < cutoff]
            for uid in stale:
                del self._last_call[uid]

        return await handler(event, data)
