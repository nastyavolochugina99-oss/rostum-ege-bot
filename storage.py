# -*- coding: utf-8 -*-
"""
Хранение слотов (из JSON) и записей учеников (SQLite).
Ученик видит только время и день, не тьютора.
"""

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# Папка данных рядом со скриптом
DATA_DIR = Path(__file__).resolve().parent / "data"
SLOTS_FILE = DATA_DIR / "slots.json"
DB_FILE = DATA_DIR / "bookings.db"


def load_slots():
    """Загружает слоты из JSON. Формат: [{"id": "...", "day": "...", "time": "...", "capacity": N}, ...]"""
    if not SLOTS_FILE.exists():
        return []
    with open(SLOTS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Создаёт таблицы записей и профилей, если их нет."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            user_id INTEGER NOT NULL,
            slot_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER NOT NULL,
            full_name TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id)
        )
    """)
    conn.commit()
    conn.close()


def get_booking_count_per_slot():
    """Возвращает словарь slot_id -> количество записей."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT slot_id, COUNT(*) AS cnt FROM bookings GROUP BY slot_id"
    ).fetchall()
    conn.close()
    return {r["slot_id"]: r["cnt"] for r in rows}


def get_user_booking(user_id: int):
    """Запись пользователя: None или {"slot_id": ..., "created_at": ...}."""
    conn = get_connection()
    row = conn.execute(
        "SELECT slot_id, created_at FROM bookings WHERE user_id = ?", (user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return {"slot_id": row["slot_id"], "created_at": row["created_at"]}


def get_user_name(user_id: int):
    """Имя пользователя из профиля или None."""
    conn = get_connection()
    row = conn.execute(
        "SELECT full_name FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return row["full_name"]


def save_user_name(user_id: int, full_name: str):
    """Сохраняет/обновляет имя пользователя."""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection()
    conn.execute(
        """INSERT INTO users (user_id, full_name, updated_at) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET full_name = ?, updated_at = ?""",
        (user_id, full_name, now, full_name, now),
    )
    conn.commit()
    conn.close()


def _tutor_key_from_slot_id(slot_id: str):
    """
    Ключ тьютора из id слота.
    Ожидается формат с суффиксом: ..._a / ..._b / ..._c.
    """
    if not slot_id or "_" not in slot_id:
        return None
    key = slot_id.rsplit("_", 1)[-1].strip().lower()
    return key if key.isalpha() else None


def _tutor_limits():
    """
    Лимиты учеников по тьюторам.
    Формат env: TUTOR_LIMITS=a:4,b:3,c:3
    По умолчанию: a=4, b=3, c=3.
    """
    raw = os.getenv("TUTOR_LIMITS", "a:4,b:3,c:3").strip()
    result = {}
    if not raw:
        return result
    for item in raw.split(","):
        item = item.strip()
        if ":" not in item:
            continue
        key, val = item.split(":", 1)
        key = key.strip().lower()
        val = val.strip()
        if key and val.isdigit():
            result[key] = int(val)
    return result


def get_booking_count_per_tutor():
    """Возвращает словарь tutor_key -> количество записей."""
    rows = get_all_bookings()
    result = {}
    for r in rows:
        key = _tutor_key_from_slot_id(r["slot_id"])
        if not key:
            continue
        result[key] = result.get(key, 0) + 1
    return result


def save_booking(user_id: int, slot_id: str) -> bool:
    """
    Записывает или обновляет запись пользователя на слот.
    Возвращает True при успехе. False, если слот уже заполнен (защита от гонки).
    """
    slots = {s["id"]: s for s in load_slots()}
    slot = slots.get(slot_id)
    if not slot:
        return False
    capacity = int(slot.get("capacity", 1))
    counts = get_booking_count_per_slot()
    tutor_limits = _tutor_limits()
    tutor_counts = get_booking_count_per_tutor()
    # Уже записанный в этот слот пользователь при смене слота освобождает место
    current = get_user_booking(user_id)
    current_in_slot = current and current["slot_id"] == slot_id
    booked = counts.get(slot_id, 0)
    if not current_in_slot and booked >= capacity:
        return False
    tutor_key = _tutor_key_from_slot_id(slot_id)
    tutor_limit = tutor_limits.get(tutor_key) if tutor_key else None
    if tutor_limit is not None:
        current_key = _tutor_key_from_slot_id(current["slot_id"]) if current else None
        current_in_tutor = current and current_key == tutor_key
        tutor_booked = tutor_counts.get(tutor_key, 0)
        if not current_in_tutor and tutor_booked >= tutor_limit:
            return False
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection()
    conn.execute(
        """INSERT INTO bookings (user_id, slot_id, created_at) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET slot_id = ?, created_at = ?""",
        (user_id, slot_id, now, slot_id, now),
    )
    conn.commit()
    conn.close()
    return True


def delete_booking(user_id: int):
    """Удаляет запись пользователя (для сброса / теста)."""
    conn = get_connection()
    conn.execute("DELETE FROM bookings WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def clear_all_data():
    """Полная очистка тестовых данных: записи и профили пользователей."""
    conn = get_connection()
    conn.execute("DELETE FROM bookings")
    conn.execute("DELETE FROM users")
    conn.commit()
    conn.close()


def get_slots_with_availability():
    """
    Список всех слотов с полями: id, day, time, capacity, booked, free.
    """
    slots = load_slots()
    counts = get_booking_count_per_slot()
    tutor_limits = _tutor_limits()
    tutor_counts = get_booking_count_per_tutor()
    result = []
    for s in slots:
        sid = s["id"]
        capacity = int(s.get("capacity", 6))
        booked = counts.get(sid, 0)
        slot_free = max(0, capacity - booked)
        tutor_key = _tutor_key_from_slot_id(sid)
        tutor_limit = tutor_limits.get(tutor_key) if tutor_key else None
        if tutor_limit is None:
            free = slot_free
        else:
            tutor_booked = tutor_counts.get(tutor_key, 0)
            tutor_free = max(0, tutor_limit - tutor_booked)
            free = min(slot_free, tutor_free)
        result.append({
            **s,
            "booked": booked,
            "free": free,
        })
    return result


def get_available_slots_for_choice():
    """Слоты, на которые ещё можно записаться (free > 0)."""
    return [s for s in get_slots_with_availability() if s["free"] > 0]


def get_days_with_available_slots():
    """Дни недели, в которые есть хотя бы один свободный слот (порядок: пн–вс)."""
    order = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
    available = get_available_slots_for_choice()
    days_set = {s["day"].lower().strip() for s in available}
    return [d for d in order if d in days_set]


def get_available_groups_for_day(day: str):
    """
    По одному пункту на (день, время): сумма свободных мест по всем слотам с этим временем.
    Возвращает список {"day", "time", "free"} без имени тьютора.
    """
    slots = get_slots_with_availability()
    day_norm = day.lower().strip()
    by_key = {}
    for s in slots:
        if s["day"].lower().strip() != day_norm or s["free"] <= 0:
            continue
        key = (s["day"], s["time"])
        by_key[key] = by_key.get(key, 0) + s["free"]
    result = [{"day": d, "time": t, "free": f} for (d, t), f in by_key.items()]
    result.sort(key=lambda x: x["time"])
    return result


def get_available_slot_ids_for_day_time(day: str, time_str: str) -> list:
    """Все slot_id с данным днём и временем, у которых есть свободное место (для повторных попыток при гонке)."""
    slots = get_available_slots_for_choice()
    day_norm = day.lower().strip()
    time_norm = time_str.strip()
    return [
        s["id"] for s in slots
        if s["day"].lower().strip() == day_norm and s["time"].strip() == time_norm
    ]


def get_any_available_slot_id_for_day_time(day: str, time_str: str):
    """Любой slot_id с данным днём и временем, у которого есть свободное место."""
    ids = get_available_slot_ids_for_day_time(day, time_str)
    return ids[0] if ids else None


def get_all_bookings():
    """Все записи: список {"user_id", "slot_id", "created_at", "full_name"}."""
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT b.user_id, b.slot_id, b.created_at, COALESCE(u.full_name, '') AS full_name
        FROM bookings b
        LEFT JOIN users u ON u.user_id = b.user_id
        """
    ).fetchall()
    conn.close()
    return [
        {
            "user_id": r["user_id"],
            "slot_id": r["slot_id"],
            "created_at": r["created_at"],
            "full_name": r["full_name"],
        }
        for r in rows
    ]
