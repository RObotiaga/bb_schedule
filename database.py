# FILE: database.py (Refactored FIX)
import aiosqlite
import logging
import json
from typing import List, Dict, Any, Tuple
from config import DB_PATH
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Инициализация и подключение ---
async def get_db_connection():
    """
    Возвращает асинхронный контекстный менеджер aiosqlite.connect(), 
    который будет ожидан при использовании async with.
    """
    # Создаем директорию для БД, если ее нет (синхронно)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # КЛЮЧЕВОЕ ИЗМЕНЕНИЕ: Не используем await здесь!
    return aiosqlite.connect(DB_PATH)

async def initialize_database():
    """Создает все необходимые таблицы, если они не существуют."""
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ: async with ожидает результат get_db_connection()
    async with await get_db_connection() as db:
        # Устанавливаем режим возврата словарей (row_factory) для удобства
        db.row_factory = aiosqlite.Row 
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY, 
                group_name TEXT,
                record_book_number TEXT,
                settings TEXT
            )
        """)
        
        # Миграция: добавляем колонку record_book_number, если её нет
        try:
            await db.execute("ALTER TABLE users ADD COLUMN record_book_number TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass

        # Миграция: добавляем колонку settings, если её нет
        try:
            await db.execute("ALTER TABLE users ADD COLUMN settings TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty TEXT,
                course TEXT,
                group_name TEXT,
                week_type TEXT,
                lesson_date TEXT,
                time TEXT,
                subject TEXT,
                teacher TEXT,
                location TEXT
            )
        """)
        
        await db.execute("""
            CREATE TABLE IF NOT EXISTS broadcast_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                message_ids_json TEXT
            )
        """)
        
        # Кэш результатов сессии
        await db.execute("""
            CREATE TABLE IF NOT EXISTS session_cache (
                record_book_number TEXT PRIMARY KEY,
                data_json TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Заметки к предметам
        await db.execute("""
            CREATE TABLE IF NOT EXISTS subject_notes (
                user_id INTEGER,
                subject_name TEXT,
                note_text TEXT,
                checklist_json TEXT,
                PRIMARY KEY (user_id, subject_name)
            )
        """)
        
        await db.commit()
    logging.info("База данных успешно инициализирована (aiosqlite).")

# --- Загрузка структуры в память (Кэширование) ---
async def load_structure_from_db() -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Загружает структуру меню и список преподавателей из БД асинхронно."""
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        db.row_factory = aiosqlite.Row

        try:
            # 1. Загрузка структуры меню
            cursor = await db.execute("SELECT DISTINCT faculty, course, group_name FROM schedule ORDER BY faculty, course, group_name")
            rows = await cursor.fetchall()
            
            temp_structured_data = {}
            for row in rows:
                faculty, course, group_name = row['faculty'], row['course'], row['group_name']
                if faculty not in temp_structured_data: temp_structured_data[faculty] = {}
                if course not in temp_structured_data[faculty]: temp_structured_data[faculty][course] = []
                if group_name not in temp_structured_data[faculty][course]: temp_structured_data[faculty][course].append(group_name)
            
            FACULTIES_LIST = sorted(temp_structured_data.keys())
            
            # 2. Загрузка списка преподавателей
            cursor_teachers = await db.execute("SELECT DISTINCT teacher FROM schedule WHERE teacher IS NOT NULL AND teacher != 'Не указан'")
            ALL_TEACHERS_LIST = sorted([row['teacher'] for row in await cursor_teachers.fetchall()])
            
            logging.info(f"Структура меню ({len(FACULTIES_LIST)} факультетов) и {len(ALL_TEACHERS_LIST)} преподавателей успешно загружены.")
            return temp_structured_data, FACULTIES_LIST, ALL_TEACHERS_LIST

        except aiosqlite.OperationalError as e:
            logging.error(f"Ошибка при загрузке структуры из БД: {e}. Таблица 'schedule' пуста или отсутствует.")
            return {}, [], []

# --- Пользователи и Группы ---
async def save_user_group_db(user_id: int, group_name: str | None):
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT INTO users (user_id, group_name) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET group_name=excluded.group_name
        """, (user_id, group_name))
        await db.commit()

async def get_user_group_db(user_id: int) -> str | None:
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        async with db.execute("SELECT group_name FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            # row[0] вместо row['group_name'] так как row_factory не применяется 
            # по умолчанию для row_factory = aiosqlite.Row в aiosqlite.fetchone() 
            # без явного установления на уровне курсора или коннекта
            return row[0] if row else None 

async def save_record_book_number(user_id: int, number: str):
    async with await get_db_connection() as db:
        # Используем UPDATE, так как пользователь уже должен существовать (создается при /start)
        # Но на всякий случай используем INSERT OR IGNORE или проверку, 
        # но логичнее предположить, что юзер уже есть.
        # Однако, если юзера нет, надо бы его создать.
        # Лучше использовать UPSERT логику, но у нас SQLite.
        # INSERT OR REPLACE может затереть group_name, если мы не передадим его.
        # Поэтому лучше сначала UPDATE.
        
        cursor = await db.execute("UPDATE users SET record_book_number = ? WHERE user_id = ?", (number, user_id))
        if cursor.rowcount == 0:
            # Если пользователя нет, создаем
            await db.execute("INSERT INTO users (user_id, record_book_number) VALUES (?, ?)", (user_id, number))
        await db.commit()

async def get_record_book_number(user_id: int) -> str | None:
    async with await get_db_connection() as db:
        async with db.execute("SELECT record_book_number FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None 

async def update_user_settings(user_id: int, settings: dict):
    async with await get_db_connection() as db:
        await db.execute("UPDATE users SET settings = ? WHERE user_id = ?", (json.dumps(settings), user_id))
        await db.commit()

async def get_user_settings(user_id: int) -> dict:
    async with await get_db_connection() as db:
        async with db.execute("SELECT settings FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                try:
                    return json.loads(row[0])
                except json.JSONDecodeError:
                    return {}
            return {} 

async def get_all_user_ids() -> List[int]:
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_all_courses() -> List[str]:
    """Возвращает отсортированный список уникальных курсов."""
    async with await get_db_connection() as db:
        cursor = await db.execute("SELECT DISTINCT course FROM schedule ORDER BY course")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

# --- Расписание ---
async def get_schedule_by_group(group: str, date_str: str):
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        db.row_factory = aiosqlite.Row # Устанавливаем явно
        async with db.execute(
            "SELECT * FROM schedule WHERE group_name = ? AND lesson_date = ? ORDER BY time", 
            (group, date_str)
        ) as cursor:
            return await cursor.fetchall()

async def get_schedule_by_teacher(teacher_name: str, date_str: str):
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        db.row_factory = aiosqlite.Row # Устанавливаем явно
        async with db.execute(
            "SELECT * FROM schedule WHERE teacher = ? AND lesson_date = ? ORDER BY time", 
            (teacher_name, date_str)
        ) as cursor:
            return await cursor.fetchall()

# --- Логирование рассылок ---
async def log_broadcast(message_ids: list):
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        await db.execute("INSERT INTO broadcast_log (message_ids_json) VALUES (?)", (json.dumps(message_ids),))
        await db.commit()

async def get_last_broadcast() -> List[tuple] | None:
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        async with db.execute("SELECT message_ids_json FROM broadcast_log ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            # row[0] вместо row['message_ids_json']
            return json.loads(row[0]) if row else None

async def delete_last_broadcast_log() -> bool:
    # КОРРЕКТНОЕ ИСПОЛЬЗОВАНИЕ
    async with await get_db_connection() as db:
        async with db.execute("SELECT id FROM broadcast_log ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            if row:
                await db.execute("DELETE FROM broadcast_log WHERE id = ?", (row[0],))
                await db.commit()
                return True
            return False

# --- Кэширование результатов сессии ---
async def get_cached_session_results(record_book_number: str) -> Tuple[List[dict] | None, str | None]:
    """
    Возвращает (data, last_updated_iso_str) или (None, None).
    """
    async with await get_db_connection() as db:
        async with db.execute("SELECT data_json, last_updated FROM session_cache WHERE record_book_number = ?", (record_book_number,)) as cursor:
            row = await cursor.fetchone()
            if row:
                try:
                    return json.loads(row[0]), row[1]
                except json.JSONDecodeError:
                    return None, None
            return None, None

async def save_cached_session_results(record_book_number: str, data: List[dict]):
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT INTO session_cache (record_book_number, data_json, last_updated) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(record_book_number) DO UPDATE SET 
                data_json=excluded.data_json, 
                last_updated=CURRENT_TIMESTAMP
        """, (record_book_number, json.dumps(data)))
        await db.commit()

# --- Заметки к предметам ---
async def get_subject_note(user_id: int, subject_name: str) -> dict:
    """
    Возвращает dict с ключами 'note_text' и 'checklist' (list).
    Если записи нет, возвращает пустую структуру.
    """
    async with await get_db_connection() as db:
        async with db.execute("SELECT note_text, checklist_json FROM subject_notes WHERE user_id = ? AND subject_name = ?", (user_id, subject_name)) as cursor:
            row = await cursor.fetchone()
            if row:
                note_text = row[0] if row[0] else ""
                checklist = []
                if row[1]:
                    try:
                        checklist = json.loads(row[1])
                    except json.JSONDecodeError:
                        checklist = []
                return {"note_text": note_text, "checklist": checklist}
            return {"note_text": "", "checklist": []}

async def save_subject_note(user_id: int, subject_name: str, note_text: str, checklist: list):
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT INTO subject_notes (user_id, subject_name, note_text, checklist_json)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, subject_name) DO UPDATE SET
                note_text=excluded.note_text,
                checklist_json=excluded.checklist_json
        """, (user_id, subject_name, note_text, json.dumps(checklist)))
        await db.commit()