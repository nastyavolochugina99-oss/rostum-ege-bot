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

# Тексты (можно вынести в конфиг)
TEXTS = {
    "welcome": (
        "Привет! Это запись на тьюторские сессии курса ЕГЭ.\n\n"
        "Сессии проходят 1 раз в две недели по 30 минут. Выбери удобное время — "
        "оно будет закреплено за тобой на весь курс."
    ),
    "btn_choose": "Выбрать время сессии",
    "btn_change": "Изменить время",
    "already_booked": "Ты уже записан на тьюторские сессии: {day} в {time}.",
    "choose_slot": "Выбери удобное окно (показаны только слоты со свободными местами):",
    "no_slots": "К сожалению, свободных мест сейчас нет. Напиши куратору или в поддержку.",
    "confirmed": (
        "Готово. Ты записан на тьюторские сессии раз в 2 недели: <b>{day} в {time}</b>.\n\n"
        "Ближайшая сессия: {next_date}. Продолжительность — 30 минут.\n"
        "Тьютор будет назначен; тебе придёт напоминание перед встречей."
    ),
    "error": "Что-то пошло не так. Попробуй ещё раз или напиши куратору.",
    "reminder": "Завтра сессия с тьютором)",
    "btn_curator": "Написать куратору",
    "curator_no_link": "Ссылка на куратора не настроена. Обратись к организаторам курса.",
    "btn_back_menu": "← В меню",
    "btn_prev_page": "← Пред.",
    "btn_next_page": "След. →",
}

# Слотов на одной странице при выборе времени
SLOTS_PER_PAGE = 6


def next_occurrence(day_name: str, time_str: str, after: datetime) -> datetime:
    """
    Ближайшая дата/время: день недели `day_name` (русский) и время `time_str`.
    Раз в 2 недели — показываем ближайшее наступление этого окна.
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

    d = after.date()
    current_weekday = d.weekday()
    days_ahead = (target_weekday - current_weekday + 7) % 7
    candidate = d + timedelta(days=days_ahead)
    candidate_dt = datetime.combine(candidate, datetime.min.time().replace(hour=hour, minute=minute))

    # Если сегодня тот же день, но время уже прошло — берём следующий раз (через 7 дней)
    if days_ahead == 0 and after >= candidate_dt:
        candidate_dt += timedelta(days=7)
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


def _slots_keyboard(page: int = 0):
    """
    Клавиатура выбора слотов с пагинацией. page — номер страницы (0-based).
    Возвращает (text, InlineKeyboardMarkup). При отсутствии слотов — текст + кнопка «В меню».
    """
    available = storage.get_available_slots_for_choice()
    if not available:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")]])
        return TEXTS["no_slots"], kb
    total = len(available)
    total_pages = (total + SLOTS_PER_PAGE - 1) // SLOTS_PER_PAGE
    page = max(0, min(page, total_pages - 1))
    start = page * SLOTS_PER_PAGE
    page_slots = available[start : start + SLOTS_PER_PAGE]
    buttons = []
    for s in page_slots:
        label = f"{s['day']} {s['time']} (осталось {s['free']})"
        buttons.append([InlineKeyboardButton(label, callback_data=f"slot:{s['id']}")])
    nav = []
    if total_pages > 1:
        if page > 0:
            nav.append(InlineKeyboardButton(TEXTS["btn_prev_page"], callback_data=f"slots_page:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton(TEXTS["btn_next_page"], callback_data=f"slots_page:{page + 1}"))
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(TEXTS["btn_back_menu"], callback_data="back_to_start")])
    return TEXTS["choose_slot"], InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработка /start: приветствие или «уже записан» + кнопки (меню)."""
    storage.init_db()
    user_id = update.effective_user.id if update.effective_user else 0
    text, keyboard = _start_keyboard(user_id)
    await update.message.reply_text(text, reply_markup=keyboard)


async def _show_slots_page(update: Update, page: int = 0) -> None:
    """Показать страницу слотов (или «нет мест» с кнопкой В меню)."""
    query = update.callback_query
    await query.answer()
    text, keyboard = _slots_keyboard(page)
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_choose_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Показать первую страницу слотов."""
    await _show_slots_page(update, 0)


async def button_slots_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Переключить на страницу слотов (callback_data = slots_page:N)."""
    if not update.callback_query.data or not update.callback_query.data.startswith("slots_page:"):
        await update.callback_query.answer()
        return
    try:
        page = int(update.callback_query.data.split(":", 1)[1])
    except (IndexError, ValueError):
        page = 0
    await _show_slots_page(update, page)


async def button_back_to_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Вернуться в меню (главный экран)."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id if update.effective_user else 0
    text, keyboard = _start_keyboard(user_id)
    await query.edit_message_text(text, reply_markup=keyboard)


async def button_slot_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Пользователь выбрал слот — сохраняем запись и отправляем подтверждение."""
    query = update.callback_query
    await query.answer()

    if not query.data or not query.data.startswith("slot:"):
        await query.edit_message_text(TEXTS["error"])
        return

    slot_id = query.data.replace("slot:", "").strip()
    user_id = update.effective_user.id if update.effective_user else 0

    slots = {s["id"]: s for s in storage.load_slots()}
    slot = slots.get(slot_id)
    if not slot:
        await query.edit_message_text(TEXTS["error"])
        return

    # Проверяем, что в слоте ещё есть место
    available = storage.get_available_slots_for_choice()
    avail_ids = {s["id"] for s in available}
    if slot_id not in avail_ids:
        await query.edit_message_text(TEXTS["no_slots"])
        return

    storage.save_booking(user_id, slot_id)
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


def main() -> None:
    token = os.getenv("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN не задан. Создай бота через @BotFather и укажи токен в .env")
        return

    storage.init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_choose_time, pattern="^choose_time$"))
    app.add_handler(CallbackQueryHandler(button_slots_page, pattern="^slots_page:"))
    app.add_handler(CallbackQueryHandler(button_back_to_start, pattern="^back_to_start$"))
    app.add_handler(CallbackQueryHandler(button_curator, pattern="^curator$"))
    app.add_handler(CallbackQueryHandler(button_slot_selected, pattern="^slot:"))

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
