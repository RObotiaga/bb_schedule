# FILE: app/core/database.py
import aiosqlite
import logging
import json
from typing import List, Dict, Any, Tuple
from app.core.config import DB_PATH
import os

# --- Инициализация и подключение ---
async def get_db_connection():
    """
    Возвращает асинхронный контекстный менеджер aiosqlite.connect(), 
    который будет ожидан при использовании async with.
    """
    # Создаем директорию для БД, если ее нет (синхронно)
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return aiosqlite.connect(DB_PATH)

async def initialize_database():
    """Создает все необходимые таблицы, если они не существуют."""
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
        
        # Миграции
        try:
            await db.execute("ALTER TABLE users ADD COLUMN record_book_number TEXT")
            await db.commit()
        except aiosqlite.OperationalError:
            pass

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
        
        # Подписки на преподавателей
        await db.execute("""
            CREATE TABLE IF NOT EXISTS teacher_subscriptions (
                user_id INTEGER,
                teacher_name TEXT,
                PRIMARY KEY (user_id, teacher_name)
            )
        """)

        # Рейтинговые данные студентов
        await db.execute("""
            CREATE TABLE IF NOT EXISTS rating_data (
                record_book TEXT PRIMARY KEY,
                enrollment_year INTEGER,
                subjects_json TEXT,
                total_subjects INTEGER DEFAULT 0,
                passed_subjects INTEGER DEFAULT 0,
                pass_rate REAL DEFAULT 0.0,
                cluster_id INTEGER,
                is_expelled INTEGER DEFAULT 0,
                last_academic_year TEXT,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        await db.commit()
    logging.info("База данных успешно инициализирована (aiosqlite).")

# --- Загрузка структуры в память (Кэширование) ---
async def load_structure_from_db() -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Загружает структуру меню и список преподавателей из БД асинхронно."""
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
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT INTO users (user_id, group_name) VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET group_name=excluded.group_name
        """, (user_id, group_name))
        await db.commit()

async def get_user_group_db(user_id: int) -> str | None:
    async with await get_db_connection() as db:
        async with db.execute("SELECT group_name FROM users WHERE user_id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None 

async def save_record_book_number(user_id: int, number: str):
    async with await get_db_connection() as db:
        cursor = await db.execute("UPDATE users SET record_book_number = ? WHERE user_id = ?", (number, user_id))
        if cursor.rowcount == 0:
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
    async with await get_db_connection() as db:
        async with db.execute("SELECT user_id FROM users") as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def get_users_with_record_books() -> List[Tuple[int, str]]:
    """Возвращает список кортежей (user_id, record_book_number) для отслеживания сессии."""
    async with await get_db_connection() as db:
        async with db.execute("SELECT user_id, record_book_number FROM users WHERE record_book_number IS NOT NULL") as cursor:
            rows = await cursor.fetchall()
            return [(row[0], row[1]) for row in rows]

async def get_all_courses() -> List[str]:
    """Возвращает отсортированный список уникальных курсов."""
    async with await get_db_connection() as db:
        cursor = await db.execute("SELECT DISTINCT course FROM schedule ORDER BY course")
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

# --- Расписание ---
async def get_schedule_by_group(group: str, date_str: str):
    async with await get_db_connection() as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute(
            "SELECT * FROM schedule WHERE group_name = ? AND lesson_date = ? ORDER BY time", 
            (group, date_str)
        ) as cursor:
            return await cursor.fetchall()

async def get_schedule_by_teacher(teacher_name: str, date_str: str):
    async with await get_db_connection() as db:
        db.row_factory = aiosqlite.Row 
        async with db.execute(
            "SELECT * FROM schedule WHERE teacher = ? AND lesson_date = ? ORDER BY time", 
            (teacher_name, date_str)
        ) as cursor:
            return await cursor.fetchall()

# --- Логирование рассылок ---
async def log_broadcast(message_ids: list):
    async with await get_db_connection() as db:
        await db.execute("INSERT INTO broadcast_log (message_ids_json) VALUES (?)", (json.dumps(message_ids),))
        await db.commit()

async def get_last_broadcast() -> List[tuple] | None:
    async with await get_db_connection() as db:
        async with db.execute("SELECT message_ids_json FROM broadcast_log ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None

async def delete_last_broadcast_log() -> bool:
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

# --- Подписки на преподавателей ---
async def subscribe_teacher(user_id: int, teacher_name: str):
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT OR IGNORE INTO teacher_subscriptions (user_id, teacher_name)
            VALUES (?, ?)
        """, (user_id, teacher_name))
        await db.commit()

async def unsubscribe_teacher(user_id: int, teacher_name: str):
    async with await get_db_connection() as db:
        await db.execute("""
            DELETE FROM teacher_subscriptions
            WHERE user_id = ? AND teacher_name = ?
        """, (user_id, teacher_name))
        await db.commit()

async def get_subscribed_teachers(user_id: int) -> List[str]:
    async with await get_db_connection() as db:
        async with db.execute("SELECT teacher_name FROM teacher_subscriptions WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [row[0] for row in rows]

async def is_subscribed_to_teacher(user_id: int, teacher_name: str) -> bool:
    async with await get_db_connection() as db:
        async with db.execute("SELECT 1 FROM teacher_subscriptions WHERE user_id = ? AND teacher_name = ?", (user_id, teacher_name)) as cursor:
            row = await cursor.fetchone()
            return bool(row)

# --- Рейтинг студентов ---

async def save_rating_record(
    record_book: str,
    enrollment_year: int,
    subjects_json: str,
    total_subjects: int,
    passed_subjects: int,
    pass_rate: float,
    last_academic_year: str,
):
    """Сохраняет или обновляет рейтинговые данные одной зачётки."""
    async with await get_db_connection() as db:
        await db.execute("""
            INSERT INTO rating_data
                (record_book, enrollment_year, subjects_json, total_subjects,
                 passed_subjects, pass_rate, last_academic_year, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(record_book) DO UPDATE SET
                subjects_json=excluded.subjects_json,
                total_subjects=excluded.total_subjects,
                passed_subjects=excluded.passed_subjects,
                pass_rate=excluded.pass_rate,
                last_academic_year=excluded.last_academic_year,
                last_updated=CURRENT_TIMESTAMP
        """, (record_book, enrollment_year, subjects_json, total_subjects,
              passed_subjects, pass_rate, last_academic_year))
        await db.commit()


async def update_rating_cluster(record_book: str, cluster_id: int, is_expelled: int):
    """Обновляет кластер и статус отчисления."""
    async with await get_db_connection() as db:
        await db.execute(
            "UPDATE rating_data SET cluster_id = ?, is_expelled = ? WHERE record_book = ?",
            (cluster_id, is_expelled, record_book),
        )
        await db.commit()


async def get_rating_position(record_book: str, scope: str = "all") -> tuple[int, int] | None:
    """
    Возвращает (позиция, всего) в рейтинге.
    scope: 'cluster' — по специальности, 'year' — по году, 'all' — все неотчисленные.
    """
    async with await get_db_connection() as db:
        # Получаем данные текущего студента
        async with db.execute(
            "SELECT pass_rate, enrollment_year, cluster_id FROM rating_data WHERE record_book = ? AND is_expelled = 0",
            (record_book,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            my_rate, my_year, my_cluster = row[0], row[1], row[2]

        # Формируем условие фильтрации
        if scope == "cluster" and my_cluster is not None:
            where = "is_expelled = 0 AND cluster_id = ?"
            params = (my_cluster,)
        elif scope == "year":
            where = "is_expelled = 0 AND enrollment_year = ?"
            params = (my_year,)
        else:
            where = "is_expelled = 0"
            params = ()

        # Позиция = сколько студентов имеют pass_rate строго больше + 1
        async with db.execute(
            f"SELECT COUNT(*) FROM rating_data WHERE {where} AND pass_rate > ?",
            (*params, my_rate),
        ) as cursor:
            position = (await cursor.fetchone())[0] + 1

        async with db.execute(
            f"SELECT COUNT(*) FROM rating_data WHERE {where}", params
        ) as cursor:
            total = (await cursor.fetchone())[0]

        return position, total


async def get_top_students(scope: str = "all", scope_value=None, limit: int = 10) -> List[dict]:
    """
    Возвращает топ студентов по pass_rate.
    scope: 'cluster', 'year', 'all'.
    """
    async with await get_db_connection() as db:
        if scope == "cluster" and scope_value is not None:
            query = "SELECT record_book, pass_rate, total_subjects, passed_subjects FROM rating_data WHERE is_expelled = 0 AND cluster_id = ? ORDER BY pass_rate DESC LIMIT ?"
            params = (scope_value, limit)
        elif scope == "year" and scope_value is not None:
            query = "SELECT record_book, pass_rate, total_subjects, passed_subjects FROM rating_data WHERE is_expelled = 0 AND enrollment_year = ? ORDER BY pass_rate DESC LIMIT ?"
            params = (scope_value, limit)
        else:
            query = "SELECT record_book, pass_rate, total_subjects, passed_subjects FROM rating_data WHERE is_expelled = 0 ORDER BY pass_rate DESC LIMIT ?"
            params = (limit,)

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {"record_book": r[0], "pass_rate": r[1], "total": r[2], "passed": r[3]}
                for r in rows
            ]


async def get_all_rating_records(enrollment_year: int = None) -> List[dict]:
    """Все записи рейтинга (для кластеризации)."""
    async with await get_db_connection() as db:
        if enrollment_year:
            query = "SELECT record_book, subjects_json, total_subjects, last_academic_year FROM rating_data WHERE enrollment_year = ?"
            params = (enrollment_year,)
        else:
            query = "SELECT record_book, subjects_json, total_subjects, last_academic_year FROM rating_data"
            params = ()

        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [
                {"record_book": r[0], "subjects_json": r[1], "total_subjects": r[2], "last_academic_year": r[3]}
                for r in rows
            ]


async def get_student_cluster_info(record_book: str) -> dict | None:
    """Возвращает кластер и год зачисления студента."""
    async with await get_db_connection() as db:
        async with db.execute(
            "SELECT cluster_id, enrollment_year, pass_rate, total_subjects, passed_subjects, is_expelled FROM rating_data WHERE record_book = ?",
            (record_book,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row:
                return None
            return {
                "cluster_id": row[0],
                "enrollment_year": row[1],
                "pass_rate": row[2],
                "total_subjects": row[3],
                "passed_subjects": row[4],
                "is_expelled": row[5],
            }


async def get_cluster_size(cluster_id: int) -> int:
    """Количество неотчисленных в кластере."""
    async with await get_db_connection() as db:
        async with db.execute(
            "SELECT COUNT(*) FROM rating_data WHERE cluster_id = ? AND is_expelled = 0",
            (cluster_id,),
        ) as cursor:
            return (await cursor.fetchone())[0]

