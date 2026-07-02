"""
Inline-меню: одно самоочищающееся сообщение с навигацией по всем разделам.

Все кнопки навигации живут в namespace "menu:*" и редактируют то же самое
сообщение (edit_text), поэтому в чате всегда остаётся ровно одно меню-сообщение,
а кнопка «⬅️ Назад» возвращает на предыдущий экран.

Дерево:
  🏠 Главное меню
   ├─ 🏎 Следующая сессия              (menu:next)   → назад в главное
   ├─ 🗓 Расписание уик-энда           (menu:schedule)
   ├─ 🏆 Зачёт пилотов                 (menu:drivers)
   ├─ 🏭 Кубок конструкторов           (menu:teams)
   └─ ⚙️ Настройки                     (menu:settings)
        ├─ 🔔 Уведомления              (menu:notify, menu:toggle:<lead>)
        └─ 🌍 Часовой пояс             (menu:tz, menu:tzset:<zone>, menu:tzcustom)
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import db
import keyboards as kb
import render
import texts
from config import config
from security import is_valid_tz

log = logging.getLogger(__name__)
router = Router(name="menu")

_VALID_LEADS = {"2h", "1h", "30min"}


class MenuTz(StatesGroup):
    waiting_custom = State()


def _tz_of(user) -> str:
    return user.timezone if user else config.default_timezone


async def _show(callback: CallbackQuery, text: str, markup) -> None:
    """Отредактировать текущее меню-сообщение (одно самоочищающееся сообщение)."""
    try:
        await callback.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        # «message is not modified» — повторный клик по тому же разделу; не ошибка.
        if "not modified" not in str(e).lower():
            log.warning("Не удалось обновить меню: %s", e)
    await callback.answer()


@router.message(Command("menu"))
async def cmd_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    u = message.from_user
    await db.upsert_user(u.id, u.username, u.full_name)
    await message.answer(texts.MENU_MAIN, parse_mode="HTML", reply_markup=kb.main_menu_kb())


@router.callback_query(F.data == "menu:main")
async def cb_main(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(callback, texts.MENU_MAIN, kb.main_menu_kb())


@router.callback_query(F.data == "menu:next")
async def cb_next(callback: CallbackQuery) -> None:
    user = await db.get_user(callback.from_user.id)
    text = await render.render_next(_tz_of(user))
    await _show(callback, text, kb.next_view_kb())


@router.callback_query(F.data == "menu:schedule")
async def cb_schedule(callback: CallbackQuery) -> None:
    user = await db.get_user(callback.from_user.id)
    text = await render.render_schedule(_tz_of(user))
    await _show(callback, text, kb.menu_back_kb("menu:main"))


@router.callback_query(F.data == "menu:drivers")
async def cb_drivers(callback: CallbackQuery) -> None:
    text = await render.render_standings()
    await _show(callback, text, kb.menu_back_kb("menu:main"))


@router.callback_query(F.data == "menu:teams")
async def cb_teams(callback: CallbackQuery) -> None:
    text = await render.render_constructors()
    await _show(callback, text, kb.menu_back_kb("menu:main"))


@router.callback_query(F.data == "menu:settings")
async def cb_settings(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await db.get_user(callback.from_user.id)
    await _show(callback, texts.MENU_SETTINGS.format(tz=_tz_of(user)), kb.settings_menu_kb())


@router.callback_query(F.data == "menu:notify")
async def cb_notify(callback: CallbackQuery) -> None:
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("Нажми /start, чтобы начать.", show_alert=True)
        return
    await _show(callback, texts.MENU_NOTIFY, kb.menu_notify_kb(user))


@router.callback_query(F.data.startswith("menu:toggle:"))
async def cb_toggle(callback: CallbackQuery) -> None:
    lead = callback.data.rsplit(":", 1)[-1]
    if lead not in _VALID_LEADS:
        await callback.answer("❌ Неверная кнопка", show_alert=True)
        return
    await db.toggle_notify(callback.from_user.id, lead)
    user = await db.get_user(callback.from_user.id)
    if user is None:
        await callback.answer("Нажми /start, чтобы начать.", show_alert=True)
        return
    try:
        await callback.message.edit_reply_markup(reply_markup=kb.menu_notify_kb(user))
    except TelegramBadRequest:
        pass
    await callback.answer("Сохранено ✅")


@router.callback_query(F.data == "menu:tz")
async def cb_tz(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await _show(callback, texts.MENU_TZ, kb.menu_timezone_kb())


@router.callback_query(F.data.startswith("menu:tzset:"))
async def cb_tzset(callback: CallbackQuery) -> None:
    tz = callback.data.split(":", 2)[2]
    if not is_valid_tz(tz):
        log.warning("Невалидный tz в меню от user %s: %r", callback.from_user.id, callback.data)
        await callback.answer("❌ Неверный часовой пояс", show_alert=True)
        return
    await db.set_timezone(callback.from_user.id, tz)
    user = await db.get_user(callback.from_user.id)
    await _show(callback, texts.MENU_SETTINGS.format(tz=_tz_of(user)), kb.settings_menu_kb())


@router.callback_query(F.data == "menu:tzcustom")
async def cb_tzcustom(callback: CallbackQuery, state: FSMContext) -> None:
    # Запоминаем координаты меню-сообщения, чтобы после ввода обновить именно его.
    await state.update_data(menu_chat=callback.message.chat.id, menu_msg=callback.message.message_id)
    await state.set_state(MenuTz.waiting_custom)
    await _show(callback, texts.TZ_CUSTOM_PROMPT, kb.menu_back_kb("menu:settings"))


@router.message(MenuTz.waiting_custom, F.text, ~F.text.startswith("/"))
async def on_menu_custom_tz(message: Message, state: FSMContext) -> None:
    tz_input = (message.text or "").strip()
    if not is_valid_tz(tz_input):
        await message.answer(texts.TZ_INVALID, parse_mode="HTML")
        return

    await db.set_timezone(message.from_user.id, tz_input)
    data = await state.get_data()
    await state.clear()

    user = await db.get_user(message.from_user.id)
    settings_text = texts.MENU_SETTINGS.format(tz=_tz_of(user))
    chat_id, msg_id = data.get("menu_chat"), data.get("menu_msg")

    # Обновляем исходное меню-сообщение (не плодим новые). Своё сообщение с
    # введённым текстом бот в личке удалить не может (ограничение Telegram) —
    # поэтому в ответ на него шлём короткое подтверждение.
    edited = False
    if chat_id and msg_id:
        try:
            await message.bot.edit_message_text(
                settings_text,
                chat_id=chat_id, message_id=msg_id,
                parse_mode="HTML", reply_markup=kb.settings_menu_kb(),
            )
            edited = True
        except TelegramBadRequest as e:
            log.warning("Не удалось обновить меню после ввода tz: %s", e)

    if edited:
        await message.answer(texts.TZ_SET_OK.format(tz=tz_input), parse_mode="HTML")
    else:
        await message.answer(settings_text, parse_mode="HTML", reply_markup=kb.settings_menu_kb())
