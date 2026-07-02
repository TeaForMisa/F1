"""Инлайн-клавиатуры: выбор часового пояса и настройки уведомлений."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db import User

TIMEZONES: list[tuple[str, str]] = [
    ("🇷🇺 Москва (UTC+3)", "Europe/Moscow"),
    ("🇷🇺 Самара (UTC+4)", "Europe/Samara"),
    ("🇷🇺 Екатеринбург (UTC+5)", "Asia/Yekaterinburg"),
    ("🇰🇿 Алматы / Астана (UTC+5)", "Asia/Almaty"),
    ("🇺🇿 Ташкент (UTC+5)", "Asia/Tashkent"),
    ("🇷🇺 Омск (UTC+6)", "Asia/Omsk"),
    ("🇷🇺 Красноярск (UTC+7)", "Asia/Krasnoyarsk"),
    ("🇷🇺 Иркутск (UTC+8)", "Asia/Irkutsk"),
    ("🇷🇺 Якутск (UTC+9)", "Asia/Yakutsk"),
    ("🇷🇺 Владивосток (UTC+10)", "Asia/Vladivostok"),
    ("🇧🇾 Минск (UTC+3)", "Europe/Minsk"),
    ("🇺🇦 Киев (UTC+2)", "Europe/Kyiv"),
    ("🇦🇪 Дубай (UTC+4)", "Asia/Dubai"),
    ("🇹🇷 Стамбул (UTC+3)", "Europe/Istanbul"),
    ("🇩🇪 Берлин / Париж (UTC+1)", "Europe/Berlin"),
    ("🇬🇧 Лондон (UTC+0)", "Europe/London"),
    ("🇺🇸 Нью-Йорк (UTC-5)", "America/New_York"),
    ("🇺🇸 Чикаго (UTC-6)", "America/Chicago"),
    ("🇺🇸 Лос-Анджелес (UTC-8)", "America/Los_Angeles"),
    ("🇧🇷 Сан-Паулу (UTC-3)", "America/Sao_Paulo"),
    ("🇯🇵 Токио (UTC+9)", "Asia/Tokyo"),
    ("🇸🇬 Сингапур (UTC+8)", "Asia/Singapore"),
    ("🇦🇺 Сидней (UTC+10)", "Australia/Sydney"),
]


def timezone_picker() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(text=label, callback_data=f"tz:{tz}")
        for label, tz in TIMEZONES
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(text="✏️ Ввести свой (IANA)", callback_data="tz:custom")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def timezone_picker_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ К списку", callback_data="tz:back")]]
    )


def settings_kb(user: User) -> InlineKeyboardMarkup:
    def btn(label: str, lead: str, on: bool) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            text=f"{label}: {'✅' if on else '❌'}",
            callback_data=f"toggle:{lead}",
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("За 2 часа", "2h", user.notify_2h)],
            [btn("За 1 час", "1h", user.notify_1h)],
            [btn("За 30 минут", "30min", user.notify_30min)],
            [InlineKeyboardButton(text="🌍 Сменить часовой пояс", callback_data="tz:back")],
        ]
    )
