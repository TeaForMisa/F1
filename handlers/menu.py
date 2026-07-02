"""
Inline-меню: одно самоочищающееся сообщение с навигацией по всем разделам.

Все кнопки навигации живут в namespace "menu:*". Текстовые экраны редактируют
одно и то же сообщение (edit_text), а страница трассы — это фото со схемой:
при переходе текст↔фото сообщение пересоздаётся, поэтому в чате всегда остаётся
ровно одно меню-сообщение.

Дерево:
  🏠 Главное меню
   ├─ 🏎 Следующая сессия              (menu:next)
   ├─ 🗓 Календарь сезона              (menu:cal)
   │    └─ этап N                      (menu:round:<N>)  — фото схемы + расписание
   │         ├─ 📺 Где смотреть        (menu:watch:<N>)
   │         └─ 📚 История трассы      (menu:hist:<N>)
   ├─ 🏆 Зачёт пилотов                 (menu:drivers)
   ├─ 🏭 Кубок конструкторов           (menu:teams)
   └─ ⚙️ Настройки                     (menu:settings)
        ├─ 🔔 Уведомления              (menu:notify, menu:toggle:<lead>)
        └─ 🌍 Часовой пояс             (menu:tz, menu:tzset:<zone>, menu:tzcustom)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

import circuits
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


def _round_from(data: str) -> int | None:
    try:
        return int(data.rsplit(":", 1)[-1])
    except (ValueError, AttributeError):
        return None


async def _show(callback: CallbackQuery, text: str, markup) -> None:
    """
    Показать текстовый экран меню.

    Если текущее сообщение — фото (страница трассы), его нельзя превратить в
    текстовое через edit_text, поэтому удаляем и отправляем новое. Так в чате
    всё равно остаётся ровно одно меню-сообщение.
    """
    msg = callback.message
    if msg.photo:
        try:
            await msg.delete()
        except Exception:
            pass
        await msg.answer(text, reply_markup=markup, parse_mode="HTML")
    else:
        try:
            await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
        except TelegramBadRequest as e:
            # «message is not modified» — повторный клик по тому же разделу; не ошибка.
            if "not modified" not in str(e).lower():
                log.warning("Не удалось обновить меню: %s", e)
    await callback.answer()


async def _update(callback: CallbackQuery, text: str, markup) -> None:
    """Обновить текущее сообщение на месте: подпись, если фото, иначе текст."""
    msg = callback.message
    try:
        if msg.photo:
            await msg.edit_caption(caption=text, reply_markup=markup, parse_mode="HTML")
        else:
            await msg.edit_text(text, reply_markup=markup, parse_mode="HTML")
    except TelegramBadRequest as e:
        if "not modified" not in str(e).lower():
            log.warning("Не удалось обновить страницу: %s", e)
    await callback.answer()


async def _to_photo(callback: CallbackQuery, photo: str, caption: str, markup) -> None:
    """Перейти с текстового экрана на фото-страницу трассы (удалить текст, прислать фото)."""
    msg = callback.message
    try:
        await msg.delete()
    except Exception:
        pass
    try:
        await msg.answer_photo(photo, caption=caption, reply_markup=markup, parse_mode="HTML")
    except Exception as e:
        # Схема не загрузилась (битый URL / CDN недоступен) — не теряем страницу,
        # показываем её текстом.
        log.warning("Не удалось отправить схему трассы (%s): %s", photo, e)
        await msg.answer(caption, reply_markup=markup, parse_mode="HTML")
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
    caption, circuit_id = await render.render_next(_tz_of(user))
    markup = kb.next_view_kb()
    photo = render.circuit_photo(circuit_id)

    if callback.message.photo:
        # Уже на фото-сообщении (нажали «Обновить») — меняем подпись на месте.
        await _update(callback, caption, markup)
    elif photo:
        await _to_photo(callback, photo, caption, markup)
    else:
        await _show(callback, caption, markup)


async def _build_calendar() -> tuple[str, object]:
    """Собрать текст и клавиатуру календаря сезона (этапы: ✅ прошедшие / 🏴 будущие)."""
    now_iso = datetime.now(timezone.utc).isoformat()
    rounds = await db.get_all_rounds()
    if not rounds:
        return texts.MENU_CALENDAR_EMPTY.format(season=config.f1_season), kb.menu_back_kb("menu:main")

    items: list[tuple[int, str]] = []
    for r in rounds:
        completed = bool(r["last_start"]) and r["last_start"] < now_iso
        info = circuits.info(r["circuit_id"]) or {}
        label = info.get("cal") or r["race_name"]
        status = "✅" if completed else (r["flag_emoji"] or "🏁")
        items.append((r["round"], f"{status} Эт.{r['round']} {label}"))
    return texts.MENU_CALENDAR.format(season=config.f1_season), kb.calendar_kb(items)


@router.message(Command("calendar"))
async def cmd_calendar(message: Message, state: FSMContext) -> None:
    await state.clear()
    text, markup = await _build_calendar()
    await message.answer(text, parse_mode="HTML", reply_markup=markup)


@router.callback_query(F.data == "menu:cal")
async def cb_calendar(callback: CallbackQuery) -> None:
    text, markup = await _build_calendar()
    await _show(callback, text, markup)


@router.callback_query(F.data.startswith("menu:round:"))
async def cb_round(callback: CallbackQuery) -> None:
    try:
        round_no = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await callback.answer("❌ Неверный этап", show_alert=True)
        return

    sessions = await db.get_round(round_no)
    if not sessions:
        await callback.answer("Нет данных по этому этапу.", show_alert=True)
        return

    user = await db.get_user(callback.from_user.id)
    caption = render.render_track_page(sessions, _tz_of(user))
    markup = kb.track_page_kb(round_no)
    image = render.circuit_photo(sessions[0]["circuit_id"])

    if callback.message.photo:
        # Уже на фото-странице (вернулись из «Где смотреть»/«История») — меняем подпись.
        await _update(callback, caption, markup)
    elif image:
        await _to_photo(callback, image, caption, markup)
    else:
        # У трассы нет официальной схемы — показываем текстом.
        await _show(callback, caption, markup)


@router.callback_query(F.data.startswith("menu:watch:"))
async def cb_watch(callback: CallbackQuery) -> None:
    round_no = _round_from(callback.data)
    if round_no is None:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    sessions = await db.get_round(round_no)
    if not sessions:
        await callback.answer("Нет данных по этому этапу.", show_alert=True)
        return
    await _update(callback, render.render_watch(sessions[0]), kb.track_sub_kb(round_no))


@router.callback_query(F.data.startswith("menu:hist:"))
async def cb_history(callback: CallbackQuery) -> None:
    round_no = _round_from(callback.data)
    if round_no is None:
        await callback.answer("❌ Ошибка", show_alert=True)
        return
    sessions = await db.get_round(round_no)
    if not sessions:
        await callback.answer("Нет данных по этому этапу.", show_alert=True)
        return
    await _update(callback, render.render_track_history(sessions[0]), kb.track_sub_kb(round_no))


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
