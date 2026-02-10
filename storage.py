# -*- coding: utf-8 -*-
"""
Хранение слотов (из JSON) и записей учеников (SQLite).
Ученик видит только время и день, не тьютора.
"""

import json
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
    """Создаёт таблицу записей, если её нет."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            user_id INTEGER NOT NULL,
            slot_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
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


def save_booking(user_id: int, slot_id: str):
    """Записывает или обновляет запись пользователя на слот."""
    now = datetime.utcnow().isoformat() + "Z"
    conn = get_connection()
    conn.execute(
        """INSERT INTO bookings (user_id, slot_id, created_at) VALUES (?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET slot_id = ?, created_at = ?""",
        (user_id, slot_id, now, slot_id, now),
    )
    conn.commit()
    conn.close()


def delete_booking(user_id: int):
    """Удаляет запись пользователя (для сброса / теста)."""
    conn = get_connection()
    conn.execute("DELETE FROM bookings WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def get_slots_with_availability():
    """
    Список слотов с полями: id, day, time, capacity, booked, free.
    Только слоты, где ещё есть свободные места.
    """
    slots = load_slots()
    counts = get_booking_count_per_slot()
    result = []
    for s in slots:
        sid = s["id"]
        capacity = int(s.get("capacity", 6))
        booked = counts.get(sid, 0)
        free = max(0, capacity - booked)
        result.append({
            **s,
            "booked": booked,
            "free": free,
        })
    return result


def get_available_slots_for_choice():
    """Слоты, на которые ещё можно записаться (free > 0)."""
    return [s for s in get_slots_with_availability() if s["free"] > 0]


def get_all_bookings():
    """Все записи: список {"user_id": int, "slot_id": str, "created_at": str}."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT user_id, slot_id, created_at FROM bookings"
    ).fetchall()
    conn.close()
    return [
        {"user_id": r["user_id"], "slot_id": r["slot_id"], "created_at": r["created_at"]}
        for r in rows
    ]
