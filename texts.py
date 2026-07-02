"""
Русские тексты и форматтеры дат/времени в часовом поясе пользователя.

БЕЗОПАСНОСТЬ: все данные из внешних источников (API, БД) экранируются через
security.esc() перед вставкой в HTML.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from security import esc

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
WEEKDAYS_RU = [
    "понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье",
]


def welcome_text(name: str | None, season: int) -> str:
    greeting = f"Привет, <b>{esc(name)}</b>! 👋\n\n" if name else "Привет! 👋\n\n"
    return (
        f"{greeting}"
        f"🏎 <b>F1 Бот {season}</b>\n"
        "<i>Расписание, зачёты и уведомления Формулы 1 у тебя в Telegram</i>\n\n"
        "Здесь ты найдёшь всё, что нужно перед гонкой: расписание сессий в твоём "
        "часовом поясе, таблицу очков после каждого этапа и уведомления о "
        "приближающемся старте.\n\n"
        "<b>Что умеет бот:</b>\n"
        "🏎 /next — ближайшая сессия: дата, трасса, обратный отсчёт\n"
        "🗓 /schedule — расписание всего гоночного уик-энда\n"
        "🏆 /standings — личный зачёт пилотов\n"
        "🏭 /constructors — кубок конструкторов\n"
        "🔔 Уведомления — за 2 часа, 1 час и 30 минут до старта\n\n"
        "Сначала выбери часовой пояс 👇"
    )


WELCOME_DONE = (
    "✅ Часовой пояс установлен: <b>{tz}</b>\n\n"
    "Теперь ты будешь получать уведомления:\n"
    "  • за <b>2 часа</b> до сессии\n"
    "  • за <b>1 час</b> до сессии\n"
    "  • за <b>30 минут</b> до сессии\n\n"
    "Уведомления можно отключать в /settings. Команды — в /help."
)

HELP = (
    "🏎 <b>F1 Бот {season}</b> — команды\n\n"
    "<b>Расписание</b>\n"
    "/next — ближайшая сессия\n"
    "/schedule — расписание гоночного уик-энда\n"
    "/standings — очки пилотов\n"
    "/constructors — очки команд\n\n"
    "<b>Настройки</b>\n"
    "/timezone — сменить часовой пояс\n"
    "/settings — управлять уведомлениями\n"
    "/cancel — отменить текущее действие\n"
    "/help — эта справка\n\n"
    "Уведомления приходят автоматически за 2 ч / 1 ч / 30 мин до каждой сессии."
)

NO_NEXT_SESSION = "Сезон {season} завершён или расписание ещё не загружено. 🏁"

SETTINGS_HEADER = (
    "⚙️ <b>Настройки уведомлений</b>\n\n"
    "Часовой пояс: <b>{tz}</b>\n\n"
    "Включи/выключи уведомления кнопками ниже:"
)


# Иконка по типу сессии (session_type из sessions_cache) — единый визуальный
# язык для уведомлений, /next и /schedule.
SESSION_ICONS = {
    "practice1": "🔧",
    "practice2": "🔧",
    "practice3": "🔧",
    "sprint_qualifying": "⏱",
    "sprint": "🏁",
    "qualifying": "⏱",
    "race": "🏆",
}


def _session_icon(session_type: str) -> str:
    return SESSION_ICONS.get(session_type, "🏎")


def notify_text(session_type, flag, race_name, session_name, lead_text, when_local, circuit, city) -> str:
    icon = _session_icon(session_type)
    return (
        f"🔔 <b>{esc(flag)} {esc(race_name)}</b>\n"
        f"{icon} <b>{esc(session_name)}</b> — через {esc(lead_text)}!\n\n"
        f"🕐 {esc(when_local)}\n"
        f"📍 {esc(circuit)}, {esc(city)}"
    )


def next_session_text(session_type, flag, race_name, session_name, when_local, circuit, city, countdown) -> str:
    icon = _session_icon(session_type)
    return (
        "⏭ <b>Ближайшая сессия</b>\n\n"
        f"{esc(flag)} <b>{esc(race_name)}</b>\n"
        f"{icon} {esc(session_name)}\n\n"
        f"🕐 {esc(when_local)}\n"
        f"📍 {esc(circuit)}, {esc(city)}\n\n"
        f"⏳ До старта: <b>{esc(countdown)}</b>"
    )


def schedule_header(flag, race_name, circuit, city) -> str:
    return (
        "🗓 <b>Гоночный уик-энд</b>\n\n"
        f"{esc(flag)} <b>{esc(race_name)}</b>\n"
        f"📍 {esc(circuit)}, {esc(city)}\n"
    )


def schedule_item(session_type, session_name, when_local) -> str:
    icon = _session_icon(session_type)
    return f"\n{icon} <b>{esc(session_name)}</b>\n    🕐 {esc(when_local)}\n"


# Обычный (проприорциональный) шрифт Telegram делает выравнивание через
# "—" невозможным — колонки "прыгают", когда имена/команды разной длины.
# Поэтому зачёты рендерятся моноширинной таблицей внутри <pre>: там ширина
# каждого символа одинакова, и пробелы-заполнители реально выравнивают колонки,
# при этом сами имена и названия команд не сокращаются.


def standings_table(season: int, rows: list[tuple]) -> str:
    """rows: список (pos, flag, name, team, pts)."""
    header = STANDINGS_HEADER.format(season=season)
    if not rows:
        return header + STANDINGS_EMPTY

    name_w = max(len(str(r[2])) for r in rows)
    team_w = max(len(str(r[3])) for r in rows)
    pts_w = max(len(str(r[4])) for r in rows)

    lines = []
    for pos, flag, name, team, pts in rows:
        name_col = esc(str(name).ljust(name_w))
        team_col = esc(str(team).ljust(team_w))
        pts_col = esc(str(pts).rjust(pts_w))
        lines.append(f"{esc(pos):>2}. {esc(flag)} {name_col}  {team_col}  {pts_col}")

    return f"{header}<pre>{chr(10).join(lines)}</pre>"


def constructors_table(season: int, rows: list[tuple]) -> str:
    """rows: список (pos, flag, name, pts)."""
    header = CONSTRUCTORS_HEADER.format(season=season)
    if not rows:
        return header + CONSTRUCTORS_EMPTY

    name_w = max(len(str(r[2])) for r in rows)
    pts_w = max(len(str(r[3])) for r in rows)

    lines = []
    for pos, flag, name, pts in rows:
        name_col = esc(str(name).ljust(name_w))
        pts_col = esc(str(pts).rjust(pts_w))
        lines.append(f"{esc(pos):>2}. {esc(flag)} {name_col}  {pts_col}")

    return f"{header}<pre>{chr(10).join(lines)}</pre>"


STANDINGS_HEADER = "🏆 <b>Личный зачёт — пилоты {season}</b>\n\n"
CONSTRUCTORS_HEADER = "🏭 <b>Кубок конструкторов {season}</b>\n\n"
STANDINGS_EMPTY = "Зачёт пилотов пока недоступен."
CONSTRUCTORS_EMPTY = "Зачёт конструкторов пока недоступен."

TZ_PICKER_TITLE = "🌍 Выбери часовой пояс:"
TZ_CUSTOM_PROMPT = (
    "✏️ Введи свой часовой пояс в формате IANA\n"
    "(например <code>Europe/Moscow</code> или <code>America/New_York</code>).\n\n"
    "Полный список: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones\n\n"
    "Передумал? Отправь /cancel."
)
TZ_SET_OK = "✅ Часовой пояс установлен: <b>{tz}</b>"
TZ_INVALID = "❌ Не знаю такой часовой пояс. Проверь написание (например, Europe/Moscow)."
TZ_PICKER_BACK = "⬅️ Назад к списку"

ERROR_GENERIC = "⚠️ Не удалось получить данные. Попробуй позже."


def fmt_dt_local(dt_utc: datetime, tz_name: str) -> str:
    local = dt_utc.astimezone(ZoneInfo(tz_name))
    weekday = WEEKDAYS_RU[local.weekday()]
    return f"{weekday}, {local.day} {MONTHS_RU[local.month - 1]}, {local.hour:02d}:{local.minute:02d}"


def fmt_countdown(dt_utc: datetime, now: datetime) -> str:
    diff = int((dt_utc - now).total_seconds())
    if diff <= 0:
        return "идёт сейчас"
    days, rem = divmod(diff, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours:
        parts.append(f"{hours} ч")
    if not days and minutes:
        parts.append(f"{minutes} мин")
    return " ".join(parts) if parts else "меньше минуты"


LEAD_TEXTS = {120: "2 часа", 60: "час", 30: "30 минут"}

ADMIN_NOT_ADMIN = "⛔️ Эта команда доступна только администраторам бота."

ADMIN_STATS = (
    "📊 <b>Статистика бота</b>\n\n"
    "👥 Пользователей: <b>{total_users}</b>\n"
    "   • активных: {active_users}\n"
    "   • на паузе: {paused_users}\n"
    "   • новых за 24ч: {new_users_24h}\n\n"
    "🔔 Уведомлений отправлено всего: <b>{total_sent}</b>\n"
    "   • за последние 24ч: {sent_24h}\n\n"
    "🗓 Сессий в кеше: {sessions_cached}\n"
)

ADMIN_USERS_HEADER = "👥 <b>Последние {count} пользователей</b>\n\n"
ADMIN_USER_ROW = "• {id} | @{username} | {full_name} | tz={tz} | paused={paused}\n"
ADMIN_NO_USERS = "Пользователей пока нет."

ADMIN_USER_DETAIL = (
    "👤 <b>Пользователь {user_id}</b>\n\n"
    "Username: @{username}\n"
    "Имя: {full_name}\n"
    "Часовой пояс: {tz}\n"
    "Создан: {created}\n"
    "Уведомления: 2ч={n2h} 1ч={n1h} 30м={n30m}\n"
    "На паузе: {paused}"
)
ADMIN_USER_NOT_FOUND = "Пользователь не найден."

ADMIN_BROADCAST_PROMPT = (
    "📢 Введи текст для рассылки всем активным пользователям.\n\n"
    "Поддерживается HTML-разметка. Отправка начнётся после твоего сообщения.\n\n"
    "Передумал? Отправь /cancel."
)
ADMIN_BROADCAST_RESULT = (
    "✅ Рассылка завершена\n\n"
    "Отправлено: <b>{sent}</b>\n"
    "Ошибок: {errors}\n"
    "Всего активных: {total}"
)

ADMIN_REFRESH_DONE = "✅ Кеш сессий обновлён: {count} записей."
ADMIN_REFRESH_FAILED = "❌ Не удалось обновить кеш. Проверь логи."

ADMIN_HELP = (
    "🔧 <b>Админ-команды</b>\n\n"
    "/stats — статистика бота\n"
    "/users — последние 20 пользователей\n"
    "/user &lt;id&gt; — инфо о пользователе\n"
    "/broadcast — рассылка всем\n"
    "/refresh — обновить кеш сессий\n"
)
