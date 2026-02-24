# -*- coding: utf-8 -*-
"""
Telegram-бот для записи на тьюторские сессии (Ростум ЕГЭ).
Ученик выбирает только время; сессии раз в 2 недели по 30 минут.
"""

import os
import logging
from datetime import datetime, timedelta, time as dt_time, timezone

from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

import storage

# Загружаем переменные из .env
load_dotenv()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _cycle_start_date() -> datetime.date:
    """
    Дата старта первой сессионной недели.
    Можно переопределить через COURSE_CYCLE_START_DATE=YYYY-MM-DD.
    """
    raw = os.getenv("COURSE_CYCLE_START_DATE", "2026-03-02").strip()
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        logger.warning(
            "Некорректная COURSE_CYCLE_START_DATE=%s, используем 2026-03-02",
            raw,
        )
        return datetime(2026, 3, 2).date()

# Тексты (можно вынести в конфиг)
TEXTS = {
    "welcome": (
        "Привет! Это бот для записи на тьюторские сессии.\n\n"
        "Тьютор помогает с мотивацией, фокусом и планом на курсе — "
        "<b>встречи онлайн, индивидуально, 30 минут, раз в 2 недели.</b>\n\n"
        "<b>Выбери одно удобное время (один слот)</b> — оно закрепляется за тобой на весь курс. "
        "Переносов и отмен нет, поэтому выбирай внимательно. "
        "Если не можешь прийти — предупреди куратора заранее."
    ),
    "btn_choose": "Выбрать время сессии",
    "already_booked": "Ты уже записан на тьюторские сессии: {day} в {time}.",
    "choose_slot": "Выбери удобное окно (свободных мест в скобках):",
    "choose_day": "Выбери день недели:",
    "no_slots": "К сожалению, свободных мест сейчас нет. Напиши куратору или в поддержку.",
    "confirmed": (
        "Готово. Ты записан на тьюторские сессии раз в 2 недели: <b>{day} в {time}</b>.\n\n"
        "Ближайшая сессия: {next_date}. Продолжительность — 30 минут.\n"
        "Тьютор будет назначен; тебе придёт напоминание перед встречей."
    ),
    "error": "Что-то пошло не так. Попробуй ещё раз или напиши куратору.",
    "reminder": "Завтра сессия с тьютором.",
    "btn_curator": "Написать куратору",
    "curator_no_link": "Ссылка на куратора не настроена. Обратись к организаторам курса.",
    "reset_done": "Готово. Твоя запись сброшена. Нажми /start — увидишь приветствие и сможешь записаться заново.",
    "list_header": "📋 Записи на тьюторские сессии ({count} чел.):\n\n",
    "list_line": "• ID {user_id} — {day} {time}\n",
    "list_empty": "Пока ни одной записи.",
    "list_denied": "Эта команда только для администратора.",
    "btn_back_menu": "← В меню",
    "btn_back_days": "← К дням",
}

def _admin_ids():
    """User_id админов из переменной ADMIN_IDS (через запятую). Только они видят /записи."""
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return set()
    ids = set()
    for s in raw.split(","):
        s = s.strip()
        if s.isdigit():
            ids.add(int(s))
    return ids


def next_occurrence(day_name: str, time_str: str, after: datetime) -> datetime:
    """
    Ближайшая дата/время по фиксированному 14-дневному циклу от COURSE_CYCLE_START_DATE.
    Например, если старт цикла 2026-03-02 (понедельник), то понедельники идут
    02.03, 16.03, 30.03, ...; вторники — 03.03, 17.03, 31.03, ...
    """
    days = {
        "понедельник": 0,
        "вторник": 1,
        "среда": 2,
        "четверг": 3,
        "пятница": 4,
        "суббота": 5,
        "воскресенье": 6,
    }
    day_lower = day_name.lower().strip()
    target_weekday = days.get(day_lower, 1)

    try:
        hour, minute = map(int, time_str.strip().split(":"))
    except (ValueError, AttributeError):
        hour, minute = 17, 0

    cycle_start = _cycle_start_date()
    base_date = cycle_start + timedelta(days=target_weekday)
    base_dt = datetime.combine(base_date, datetime.min.time().replace(hour=hour, minute=minute))

    period = timedelta(days=14)
    if after <= base_dt:
        return base_dt

    # Берём ближайшую дату в 14-дневной сетке, которая не раньше `after`.
    steps = (after - base_dt) // period
    candidate_dt = base_dt + steps * period
    if candidate_dt < after:
        candidate_dt += period
    return candidate_dt


def is_session_day_tomorrow(slot_day: str, slot_time: str, created_at_iso: str, tomorrow_date) -> bool:
    """
    Сессии раз в 2 недели. Завтра — день сессии для этой записи?
    """
    try:
        created_at = datetime.fromisoformat(created_at_iso.replace("Z", "+00:00"))
        if created_at.tzinfo:
            created_at = created_at.replace(tzinfo=None)
    except (ValueError, TypeError):
        return False
    first_session_dt = next_occurrence(slot_day, slot_time, created_at)
    first_date = first_session_dt.date()
    delta = (tomorrow_date - first_date).days
    return delta >= 0 and delta % 14 == 0


async def send_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ежедневная задача: отправить «Завтра сессия с тьютором» тем, у кого завтра сессия."""
    now = datetime.now(timezone.utc)
    tomorrow = (now.date() + timedelta(days=1))
    slots_by_id = {s["id"]: s for s in storage.load_slots()}
    for row in storage.get_all_bookings():
        slot = slots_by_id.get(row["slot_id"])
        if not slot:
            continue
        if not is_session_day_tomorrow(slot["day"], slot["time"], row["created_at"], tomorrow):
            continue
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=TEXTS["reminder"],
            )
        except Exception as e:
            logger.warning("Не удалось отправить напоминание user_id=%s: %s", row["user_id"], e)


def _curator_button():
    """Кнопка «Написать куратору»: ссылка из .env или callback."""
    link = os.getenv("CURATOR_LINK", "").strip()
    if link:
        return InlineKeyboardButton(TEXTS["btn_curator"], url=link)
    return InlineKeyboardButton(TEXTS["btn_curator"], callback_data="curator")


def _start_keyboard(user_id: int):
    """
    Текст и клавиатура главного экрана (меню): приветствие или «уже записан» + кнопки.
    Возвращает (text, InlineKeyboardMarkup).
    """
    booking = storage.get_user_booking(user_id)
    slots = {s["id"]: s for s in storage.load_slots()}
    if booking:
        slot = slots.get(booking["slot_id"])
        if slot:
            text = TEXTS["already_booked"].format(day=slot["day"], time=slot["time"])
            return text, InlineKeyboardMarkup([[_curator_button()]])
    text = TEXTS["welcome"]
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(TEXTS["btn_choose"], callback_data="choose_time")],
        [_curator_button()],
    ])
    return text, keyboard


def _days_keyboard():
    """Клавиатура выбора дня недели (только дни со свободными слотами)."""
    days = storage.get_days_with_available_slots()
    if not days:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")]])
        return TEXTS["no_slots"], kb
    buttons = [[InlineKeyboardButton(d.capitalize(), callback_data=f"day:{d}")] for d in days]
    buttons.append([InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")])
    return TEXTS["choose_day"], InlineKeyboardMarkup(buttons)


def _times_keyboard(day: str):
    """Клавиатура выбора времени в выбранный день (группы по день+время, без имени тьютора)."""
    groups = storage.get_available_groups_for_day(day)
    if not groups:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(TEXTS["btn_back_days"], callback_data="back_to_days")],
            [InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")],
        ])
        return TEXTS["no_slots"], kb
    buttons = []
    for g in groups:
        label = f"{g['time']} (осталось {g['free']})"
        # callback_data до 64 байт; разделитель |
        cb = f"dt|{g['day']}|{g['time']}"
        buttons.append([InlineKeyboardButton(label, callback_data=cb)])
    buttons.append([InlineKeyboardButton(TEXTS["btn_back_days"], callback_data="back_to_days")])
    buttons.append([InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")])
    return TEXTS["choose_slot"], InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка /start: приветствие или «уже записан» + кнопки (меню)."""
    storage.init_db()
    user_id = update.effective_user.id if update.effective_user else 0
    text, keyboard = _start_keyboard(user_id)
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def _show_times_for_day(update: Update, day: str) -> None:
    """Показать выбор времени для выбранного дня."""
    query = update.callback_query
    await query.answer()
    text, keyboard = _times_keyboard(day)
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать выбор дня недели."""
    query = update.callback_query
    await query.answer()
    text, keyboard = _days_keyboard()
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Выбран день — показать время в этот день (callback_data = day:день)."""
    data = update.callback_query.data or ""
    if not data.startswith("day:"):
        await update.callback_query.answer()
        return
    day = data.split(":", 1)[1].strip()
    await _show_times_for_day(update, day)


async def button_back_to_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться к выбору дня."""
    query = update.callback_query
    await query.answer()
    text, keyboard = _days_keyboard()
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться в меню (главный экран)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    text, keyboard = _start_keyboard(user_id)
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def button_slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал слот (slot:id или dt|день|время) — сохраняем запись и подтверждаем."""
    query = update.callback_query
    await query.answer()

    data = (query.data or "").strip()
    user_id = update.effective_user.id if update.effective_user else 0
    slot_id = None
    if data.startswith("slot:"):
        slot_id = data.replace("slot:", "").strip()
    elif data.startswith("dt|"):
        parts = data.split("|", 2)
        if len(parts) >= 3:
            day, time_str = parts[1].strip(), parts[2].strip()
            for sid in storage.get_available_slot_ids_for_day_time(day, time_str):
                if storage.save_booking(user_id, sid):
                    slot_id = sid
                    break
            if not slot_id:
                await query.edit_message_text(TEXTS["no_slots"])
                return

    if not slot_id:
        await query.edit_message_text(TEXTS["error"])
        return

    slots = {s["id"]: s for s in storage.load_slots()}
    slot = slots.get(slot_id)
    if not slot:
        await query.edit_message_text(TEXTS["error"])
        return

    # Для выбора по конкретному slot:id — проверяем наличие места и сохраняем (с защитой от гонки)
    if data.startswith("slot:"):
        if not storage.save_booking(user_id, slot_id):
            await query.edit_message_text(TEXTS["no_slots"])
            return
    now = datetime.utcnow()
    next_dt = next_occurrence(slot["day"], slot["time"], now)
    next_date = next_dt.strftime("%d.%m.%Y")

    text = TEXTS["confirmed"].format(
        day=slot["day"],
        time=slot["time"],
        next_date=next_date,
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")]
    ])
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)


async def button_curator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Если ссылки на куратора нет в .env — показываем подсказку."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(TEXTS["curator_no_link"])


async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /reset: удалить свою запись и снова увидеть приветствие (для теста или сброса)."""
    user_id = update.effective_user.id if update.effective_user else 0
    storage.delete_booking(user_id)
    await update.message.reply_text(TEXTS["reset_done"])


async def zapisi_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /zapisi: список всех записей (только для админов из ADMIN_IDS)."""
    user_id = update.effective_user.id if update.effective_user else 0
    if user_id not in _admin_ids():
        await update.message.reply_text(TEXTS["list_denied"])
        return
    day_order = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    slots_by_id = {s["id"]: s for s in storage.load_slots()}
    bookings = storage.get_all_bookings()
    if not bookings:
        await update.message.reply_text(TEXTS["list_empty"])
        return

    def _booking_sort_key(b):
        s = slots_by_id.get(b["slot_id"], {})
        d, t = s.get("day", "?"), s.get("time", "?")
        return (day_order.index(d) if d in day_order else 99, t)

    lines = [TEXTS["list_header"].format(count=len(bookings))]
    for b in sorted(bookings, key=_booking_sort_key):
        slot = slots_by_id.get(b["slot_id"], {})
        day = slot.get("day", "?")
        time = slot.get("time", "?")
        lines.append(TEXTS["list_line"].format(user_id=b["user_id"], day=day, time=time))
    text = "".join(lines).strip()
    await update.message.reply_text(text)


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не задан. Создай бота через @BotFather и укажи токен в .env")
        return

    storage.init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(CommandHandler("zapisi", zapisi_cmd))
    app.add_handler(CallbackQueryHandler(button_choose_time, pattern="^choose_time$"))
    app.add_handler(CallbackQueryHandler(button_day_selected, pattern="^day:"))
    app.add_handler(CallbackQueryHandler(button_back_to_days, pattern="^back_to_days$"))
    app.add_handler(CallbackQueryHandler(button_back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(button_curator, pattern="^curator$"))
    app.add_handler(CallbackQueryHandler(button_slot_selected, pattern="^slot:|^dt\\|"))

    # Напоминание раз в день (07:00 UTC ≈ 10:00 МСК): завтра сессия с тьютором
    if app.job_queue:
        app.job_queue.run_daily(
            send_reminders,
            time=dt_time(7, 0, 0, tzinfo=timezone.utc),
        )

    logger.info("Бот запущен")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
