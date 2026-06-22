import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "database.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Chat History Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone_number TEXT NOT NULL,
        role TEXT NOT NULL,  -- 'user' o 'assistant'
        content TEXT NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 2. Bookings Table (for reminders and dashboard)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bookings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cal_booking_id TEXT UNIQUE,
        client_name TEXT NOT NULL,
        client_phone TEXT NOT NULL,
        client_email TEXT,
        start_time TEXT NOT NULL,  -- ISO format: YYYY-MM-DDTHH:MM:SSZ
        status TEXT NOT NULL,      -- 'confirmed', 'cancelled', etc.
        reminder_24h_sent INTEGER DEFAULT 0,
        reminder_2h_sent INTEGER DEFAULT 0,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    # 3. Agent Logs Table (for dashboard visualization)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS agent_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone_number TEXT,
        action TEXT NOT NULL,
        details TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()

# --- Chat History Functions ---
def add_chat_message(phone_number: str, role: str, content: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (phone_number, role, content) VALUES (?, ?, ?)",
        (phone_number, role, content)
    )
    conn.commit()
    conn.close()

def get_chat_history(phone_number: str, limit: int = 15):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM chat_history WHERE phone_number = ? ORDER BY timestamp DESC LIMIT ?",
        (phone_number, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    # Volver a ordenar cronológicamente (ascendente)
    history = [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    return history

def clear_chat_history(phone_number: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE phone_number = ?", (phone_number,))
    conn.commit()
    conn.close()

# --- Bookings Functions ---
def save_booking(cal_booking_id: str, name: str, phone: str, email: str, start_time: str, status: str = "confirmed"):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO bookings (cal_booking_id, client_name, client_phone, client_email, start_time, status)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(cal_booking_id) DO UPDATE SET
        client_name=excluded.client_name,
        client_phone=excluded.client_phone,
        client_email=excluded.client_email,
        start_time=excluded.start_time,
        status=excluded.status
    """, (cal_booking_id, name, phone, email, start_time, status))
    conn.commit()
    conn.close()

def get_booking(cal_booking_id: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM bookings WHERE cal_booking_id = ?", (cal_booking_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_upcoming_bookings_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    # Próximas citas ordenadas
    cursor.execute(
        "SELECT * FROM bookings WHERE status = 'confirmed' ORDER BY start_time ASC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

def mark_reminder_sent(booking_id: int, type_hours: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    if type_hours == "24h":
        cursor.execute("UPDATE bookings SET reminder_24h_sent = 1 WHERE id = ?", (booking_id,))
    elif type_hours == "2h":
        cursor.execute("UPDATE bookings SET reminder_2h_sent = 1 WHERE id = ?", (booking_id,))
    conn.commit()
    conn.close()

# --- Logs Functions ---
def add_log(phone_number: str, action: str, details: str = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO agent_logs (phone_number, action, details) VALUES (?, ?, ?)",
        (phone_number, action, details)
    )
    conn.commit()
    conn.close()

def get_recent_logs(limit: int = 50):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM agent_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Initialize Database on load
init_db()
