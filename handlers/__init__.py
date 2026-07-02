"""Регистрация всех роутеров обработчиков в диспетчере."""
from __future__ import annotations

from aiogram import Dispatcher

from . import admin, common, info, menu, settings, timezone


def register(dp: Dispatcher) -> None:
    dp.include_router(admin.router)
    dp.include_router(common.router)
    dp.include_router(menu.router)
    dp.include_router(timezone.router)
    dp.include_router(settings.router)
    dp.include_router(info.router)
