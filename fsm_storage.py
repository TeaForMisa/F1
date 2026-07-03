"""
Персистентное FSM-хранилище aiogram на SQLite.

Зачем: стандартный MemoryStorage теряет состояния диалогов при каждом рестарте.
На Bothost контейнер пересобирается при каждом обновлении из Git, поэтому
пользователь, начавший ввод часового пояса (или админ - рассылку), после
деплоя оказывался «в подвешенном» состоянии. Эта реализация хранит состояния
в той же персистентной БД (/app/data), что и настройки пользователей.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StateType, StorageKey

import db

log = logging.getLogger(__name__)


def _key_str(key: StorageKey) -> str:
    return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.thread_id}:{key.destiny}"


class SQLiteStorage(BaseStorage):
    """FSM-хранилище поверх таблицы fsm_storage (см. db.py)."""

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        await db.fsm_set_state(_key_str(key), value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return await db.fsm_get_state(_key_str(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        await db.fsm_set_data(_key_str(key), json.dumps(data, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        raw = await db.fsm_get_data(_key_str(key))
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            log.warning("Повреждённые FSM-данные для ключа %s - сброшены", _key_str(key))
            return {}

    async def close(self) -> None:
        # Соединения открываются на операцию (см. db._connect) - закрывать нечего.
        pass
