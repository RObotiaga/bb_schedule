# FILE: app/core/database.py
import aiosqlite
import logging
import json
from typing import List, Dict, Any, Tuple
from app.core.config import DB_PATH
import os

_global_db_conn = None

async def get_db_connection():
    """Возвращает глобальное подключение к БД."""
    global _global_db_conn
    if _global_db_conn is None:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        _global_db_conn = await aiosqlite.connect(DB_PATH)
        await _global_db_conn.execute("PRAGMA journal_mode=WAL;")
        await _global_db_conn.execute("PRAGMA synchronous=NORMAL;")
        _global_db_conn.row_factory = aiosqlite.Row
    return _global_db_conn

async def close_db_connection():
    """Закрывает глобальное подключение (для тестов/завершения)."""
    global _global_db_conn
    if _global_db_conn is not None:
        await _global_db_conn.close()
        _global_db_conn = None

async def initialize_database():
    """Создает все необходимые таблицы, если они не существуют."""
    db = await get_db_connection()
        
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
        
    try:
        await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        await db.commit()
    except aiosqlite.OperationalError:
        pass
        
    try:
        await db.execute("ALTER TABLE users ADD COLUMN first_name TEXT")
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
    
    # Отчисленные студенты
    await db.execute("""
        CREATE TABLE IF NOT EXISTS expelled_students (
            record_book TEXT PRIMARY KEY,
            enrollment_year INTEGER,
            expelled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            cluster_id INTEGER
        )
    """)
    
    # Миграция: перенос существующих отчисленных в новую таблицу
    try:
        await db.execute("""
            INSERT OR IGNORE INTO expelled_students (record_book, enrollment_year, cluster_id)
            SELECT record_book, enrollment_year, cluster_id
            FROM rating_data
            WHERE is_expelled = 1
        """)
        await db.execute("DELETE FROM rating_data WHERE is_expelled = 1")
        await db.commit()
    except aiosqlite.OperationalError as e:
        logging.error(f"Migration expelled_students error: {e}")
    
    # --- Индексы для оптимизации выборок ---
    await db.execute("CREATE INDEX IF NOT EXISTS idx_group_date ON schedule (group_name, lesson_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_faculty_course_group ON schedule (faculty, course, group_name)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_teacher_date ON schedule (teacher, lesson_date)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rating_cluster ON rating_data (cluster_id)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_rating_year ON rating_data (enrollment_year)")

    # Маппинг кластеров на реальные группы расписания
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cluster_groups (
            cluster_id INTEGER PRIMARY KEY,
            group_name TEXT NOT NULL,
            similarity REAL DEFAULT 0.0
        )
    """)

    # Статистика закрываемости предметов
    await db.execute("""
        CREATE TABLE IF NOT EXISTS subject_global_stats (
            subject TEXT PRIMARY KEY,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS cluster_subject_stats (
            cluster_id INTEGER,
            subject TEXT,
            total_students INTEGER DEFAULT 0,
            passed_students INTEGER DEFAULT 0,
            pass_rate REAL DEFAULT 0.0,
            total_persons INTEGER DEFAULT 0,
            passed_persons INTEGER DEFAULT 0,
            person_pass_rate REAL DEFAULT 0.0,
            PRIMARY KEY (cluster_id, subject)
        )
    """)

    # Миграции для новых колонок (persons)
    for table in ["subject_global_stats", "cluster_subject_stats"]:
        for col in ["total_persons", "passed_persons"]:
            try:
                await db.execute(f"ALTER TABLE {table} ADD COLUMN {col} INTEGER DEFAULT 0")
                await db.commit()
            except aiosqlite.OperationalError:
                pass
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN person_pass_rate REAL DEFAULT 0.0")
            await db.commit()
        except aiosqlite.OperationalError:
            pass


    # Таблица для логирования фоновых задач (статусы бота)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_name TEXT,
            start_time TIMESTAMP,
            end_time TIMESTAMP,
            status TEXT,
            details_json TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_name ON job_logs (job_name)")

    await db.commit()
    logging.info("База данных успешно инициализирована (aiosqlite).")

# --- Загрузка структуры в память (Кэширование) ---
async def load_structure_from_db() -> Tuple[Dict[str, Any], List[str], List[str]]:
    """Загружает структуру меню и список преподавателей из БД асинхронно."""
    db = await get_db_connection()

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
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO users (user_id, group_name) VALUES (?, ?)
        ON CONFLICT(user_id) DO UPDATE SET group_name=excluded.group_name
    """, (user_id, group_name))
    await db.commit()

async def get_user_group_db(user_id: int) -> str | None:
    db = await get_db_connection()
    async with db.execute("SELECT group_name FROM users WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else None 

async def save_record_book_number(user_id: int, number: str, username: str | None = None, first_name: str | None = None):
    db = await get_db_connection()
    cursor = await db.execute(
        "UPDATE users SET record_book_number = ?, username = ?, first_name = ? WHERE user_id = ?", 
        (number, username, first_name, user_id)
    )
    if cursor.rowcount == 0:
        await db.execute(
            "INSERT INTO users (user_id, record_book_number, username, first_name) VALUES (?, ?, ?, ?)", 
            (user_id, number, username, first_name)
        )
    await db.commit()

async def get_record_book_number(user_id: int) -> str | None:
    db = await get_db_connection()
    async with db.execute("SELECT record_book_number FROM users WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else None 

async def update_user_settings(user_id: int, settings: dict):
    db = await get_db_connection()
    await db.execute("UPDATE users SET settings = ? WHERE user_id = ?", (json.dumps(settings), user_id))
    await db.commit()

async def get_user_settings(user_id: int) -> dict:
    db = await get_db_connection()
    async with db.execute("SELECT settings FROM users WHERE user_id = ?", (user_id,)) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                return json.loads(row[0])
            except json.JSONDecodeError:
                return {}
        return {} 

async def get_all_user_ids() -> List[int]:
    db = await get_db_connection()
    async with db.execute("SELECT user_id FROM users") as cursor:
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def get_users_with_record_books() -> List[Tuple[int, str]]:
    """Возвращает список кортежей (user_id, record_book_number) для отслеживания сессии."""
    db = await get_db_connection()
    async with db.execute("SELECT user_id, record_book_number FROM users WHERE record_book_number IS NOT NULL") as cursor:
        rows = await cursor.fetchall()
        return [(row[0], row[1]) for row in rows]


async def get_users_by_record_book(record_book: str) -> List[dict]:
    """Возвращает данные пользователей (id, username, first_name), привязанных к зачётке."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT user_id, username, first_name FROM users WHERE record_book_number = ?", (record_book,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [{"user_id": r[0], "username": r[1], "first_name": r[2]} for r in rows]

async def get_all_courses() -> List[str]:
    """Возвращает отсортированный список уникальных курсов."""
    db = await get_db_connection()
    cursor = await db.execute("SELECT DISTINCT course FROM schedule ORDER BY course")
    rows = await cursor.fetchall()
    return [row[0] for row in rows]

# --- Расписание ---
async def get_schedule_by_group(group: str, date_str: str):
    db = await get_db_connection()
    async with db.execute(
        "SELECT * FROM schedule WHERE group_name = ? AND lesson_date = ? ORDER BY time", 
        (group, date_str)
    ) as cursor:
        return await cursor.fetchall()

async def get_schedule_by_teacher(teacher_name: str, date_str: str):
    db = await get_db_connection()
    async with db.execute(
        "SELECT * FROM schedule WHERE teacher = ? AND lesson_date = ? ORDER BY time", 
        (teacher_name, date_str)
    ) as cursor:
        return await cursor.fetchall()

# --- Логирование рассылок ---
async def log_broadcast(message_ids: list):
    db = await get_db_connection()
    await db.execute("INSERT INTO broadcast_log (message_ids_json) VALUES (?)", (json.dumps(message_ids),))
    await db.commit()

async def get_last_broadcast() -> List[tuple] | None:
    db = await get_db_connection()
    async with db.execute("SELECT message_ids_json FROM broadcast_log ORDER BY id DESC LIMIT 1") as cursor:
            row = await cursor.fetchone()
            return json.loads(row[0]) if row else None

async def delete_last_broadcast_log() -> bool:
    db = await get_db_connection()
    async with db.execute("SELECT id FROM broadcast_log ORDER BY id DESC LIMIT 1") as cursor:
        row = await cursor.fetchone()
        if row:
            await db.execute("DELETE FROM broadcast_log WHERE id = ?", (row[0],))
            await db.commit()
            return True
        return False


# --- Логирование фоновых задач (job_logs) ---
from datetime import datetime

async def save_job_log(job_name: str, start_time: datetime, end_time: datetime, status: str, details: dict):
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO job_logs (job_name, start_time, end_time, status, details_json) 
        VALUES (?, ?, ?, ?, ?)
    """, (
        job_name, 
        start_time.isoformat(), 
        end_time.isoformat(), 
        status, 
        json.dumps(details, ensure_ascii=False)
    ))
    await db.commit()

async def get_last_two_job_logs(job_name: str) -> List[dict]:
    db = await get_db_connection()
    async with db.execute(
        "SELECT start_time, end_time, status, details_json FROM job_logs WHERE job_name = ? ORDER BY start_time DESC LIMIT 2", 
        (job_name,)
    ) as cursor:
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "start_time": datetime.fromisoformat(row["start_time"]),
                "end_time": datetime.fromisoformat(row["end_time"]),
                "status": row["status"],
                "details": json.loads(row["details_json"])
            })
        return result

async def cleanup_old_job_logs(days: int = 30):
    db = await get_db_connection()
    await db.execute("DELETE FROM job_logs WHERE start_time < datetime('now', ?)", (f"-{days} days",))
    await db.commit()

# --- Кэширование результатов сессии ---
async def get_cached_session_results(record_book_number: str) -> Tuple[List[dict] | None, str | None]:
    db = await get_db_connection()
    async with db.execute("SELECT data_json, last_updated FROM session_cache WHERE record_book_number = ?", (record_book_number,)) as cursor:
        row = await cursor.fetchone()
        if row:
            try:
                return json.loads(row[0]), row[1]
            except json.JSONDecodeError:
                return None, None
        return None, None

async def save_cached_session_results(record_book_number: str, data: List[dict]):
    db = await get_db_connection()
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
    db = await get_db_connection()
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
    db = await get_db_connection()
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
    db = await get_db_connection()
    await db.execute("""
        INSERT OR IGNORE INTO teacher_subscriptions (user_id, teacher_name)
        VALUES (?, ?)
    """, (user_id, teacher_name))
    await db.commit()

async def unsubscribe_teacher(user_id: int, teacher_name: str):
    db = await get_db_connection()
    await db.execute("""
        DELETE FROM teacher_subscriptions
        WHERE user_id = ? AND teacher_name = ?
    """, (user_id, teacher_name))
    await db.commit()

async def get_subscribed_teachers(user_id: int) -> List[str]:
    db = await get_db_connection()
    async with db.execute("SELECT teacher_name FROM teacher_subscriptions WHERE user_id = ?", (user_id,)) as cursor:
        rows = await cursor.fetchall()
        return [row[0] for row in rows]

async def is_subscribed_to_teacher(user_id: int, teacher_name: str) -> bool:
    db = await get_db_connection()
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
    db = await get_db_connection()
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
    db = await get_db_connection()
    await db.execute(
        "UPDATE rating_data SET cluster_id = ?, is_expelled = ? WHERE record_book = ?",
        (cluster_id, is_expelled, record_book),
    )
    await db.commit()


# --- Отчисленные студенты ---

async def is_student_expelled_in_db(record_book: str) -> bool:
    """Проверяет, есть ли студент в таблице отчисленных."""
    db = await get_db_connection()
    async with db.execute("SELECT 1 FROM expelled_students WHERE record_book = ?", (record_book,)) as cursor:
        row = await cursor.fetchone()
        return bool(row)

async def save_expelled_student(record_book: str, enrollment_year: int, cluster_id: int):
    """Сохраняет студента как отчисленного и удаляет из основного рейтинга."""
    db = await get_db_connection()
    await db.execute("""
        INSERT OR IGNORE INTO expelled_students (record_book, enrollment_year, cluster_id)
        VALUES (?, ?, ?)
    """, (record_book, enrollment_year, cluster_id))
    await db.execute("DELETE FROM rating_data WHERE record_book = ?", (record_book,))
    await db.commit()

async def get_expelled_statistics() -> dict:
    """Возвращает статистику по отчисленным студентам (с начала года, семестра, всего)."""
    db = await get_db_connection()
    
    # Используем import datetime локально, если он еще не импортирован на уровне модуля
    from datetime import datetime
    now = datetime.now()
    
    # Считаем начало учебного года (если сейчас до сентября, то прошлый год)
    year_start_year = now.year if now.month >= 9 else now.year - 1
    year_start = f"{year_start_year}-09-01 00:00:00"
    
    # Считаем начало семестра (весенний с 1 февраля, осенний с 1 сентября)
    sem_start_month = "02" if now.month < 9 and now.month >= 2 else "09"
    sem_start_year = now.year if sem_start_month == "09" and now.month >= 9 else (now.year if sem_start_month == "02" else now.year - 1)
    sem_start = f"{sem_start_year}-{sem_start_month}-01 00:00:00"

    async with db.execute("SELECT COUNT(*) FROM expelled_students WHERE expelled_at >= ?", (year_start,)) as cursor:
        since_year_start = (await cursor.fetchone())[0]

    async with db.execute("SELECT COUNT(*) FROM expelled_students WHERE expelled_at >= ?", (sem_start,)) as cursor:
        since_semester_start = (await cursor.fetchone())[0]

    async with db.execute("SELECT COUNT(*) FROM expelled_students") as cursor:
        total = (await cursor.fetchone())[0]

    async with db.execute("SELECT record_book FROM expelled_students ORDER BY record_book") as cursor:
        rows = await cursor.fetchall()
        all_record_books = [r[0] for r in rows]

    return {
        "since_year_start": since_year_start,
        "since_semester_start": since_semester_start,
        "total": total,
        "all_record_books": all_record_books
    }


async def get_rating_position(record_book: str, scope: str = "all") -> tuple[int, int] | None:
    """
    Возвращает (позиция, всего) в рейтинге.
    scope: 'cluster' — по специальности, 'year' — по году, 'all' — все неотчисленные.
    """
    db = await get_db_connection()
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
    db = await get_db_connection()
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
    db = await get_db_connection()
    if enrollment_year:
        query = "SELECT record_book, subjects_json, total_subjects, last_academic_year, cluster_id, is_expelled FROM rating_data WHERE enrollment_year = ?"
        params = (enrollment_year,)
    else:
        query = "SELECT record_book, subjects_json, total_subjects, last_academic_year, cluster_id, is_expelled FROM rating_data"
        params = ()

    async with db.execute(query, params) as cursor:
        rows = await cursor.fetchall()
        return [
            {"record_book": r[0], "subjects_json": r[1], "total_subjects": r[2], "last_academic_year": r[3], "cluster_id": r[4], "is_expelled": r[5]}
            for r in rows
        ]


async def get_student_cluster_info(record_book: str) -> dict | None:
    """Возвращает кластер и год зачисления студента."""
    db = await get_db_connection()
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
    db = await get_db_connection()
    async with db.execute(
        "SELECT COUNT(*) FROM rating_data WHERE cluster_id = ? AND is_expelled = 0",
        (cluster_id,),
    ) as cursor:
        return (await cursor.fetchone())[0]


# --- Маппинг кластеров → группы ---

async def save_cluster_group(cluster_id: int, group_name: str, similarity: float):
    """Сохраняет связь кластера с группой расписания."""
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO cluster_groups (cluster_id, group_name, similarity)
        VALUES (?, ?, ?)
        ON CONFLICT(cluster_id) DO UPDATE SET
            group_name=excluded.group_name,
            similarity=excluded.similarity
    """, (cluster_id, group_name, similarity))
    await db.commit()


async def get_group_by_cluster(cluster_id: int) -> str | None:
    """Возвращает имя группы по cluster_id."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT group_name FROM cluster_groups WHERE cluster_id = ?",
        (cluster_id,),
    ) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else None


async def get_all_cluster_groups() -> List[dict]:
    """Все маппинги кластер → группа."""
    db = await get_db_connection()
    async with db.execute("SELECT cluster_id, group_name, similarity FROM cluster_groups") as cursor:
        rows = await cursor.fetchall()
        return [{"cluster_id": r[0], "group_name": r[1], "similarity": r[2]} for r in rows]

async def get_cluster_subjects(cluster_id: int) -> set:
    """Возвращает множество предметов для кластера из rating_data."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT subjects_json FROM rating_data WHERE cluster_id = ? AND is_expelled = 0 LIMIT 1",
        (cluster_id,),
    ) as cursor:
        row = await cursor.fetchone()
        if not row or not row[0]:
            return set()
        try:
            subjects = json.loads(row[0])
            return {item["subject"] for item in subjects if item.get("subject")}
        except (json.JSONDecodeError, KeyError):
            return set()

async def get_all_distinct_clusters() -> List[int]:
    """Все уникальные cluster_id из rating_data."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT DISTINCT cluster_id FROM rating_data WHERE cluster_id IS NOT NULL AND is_expelled = 0"
    ) as cursor:
        rows = await cursor.fetchall()
        return [r[0] for r in rows]

async def get_schedule_groups_subjects() -> Dict[str, set]:
    """Возвращает {group_name: {subjects...}} из расписания."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT DISTINCT group_name, subject FROM schedule WHERE subject IS NOT NULL"
    ) as cursor:
        rows = await cursor.fetchall()
        result: Dict[str, set] = {}
        for row in rows:
            group = row[0]
            if group not in result:
                result[group] = set()
            result[group].add(row[1])
        return result


# --- Рейтинг по предметам ---

async def clear_subject_global_stats():
    """Очищает таблицы перед пересчётом статистик."""
    db = await get_db_connection()
    await db.execute("DELETE FROM subject_global_stats")
    await db.execute("DELETE FROM cluster_subject_stats")
    await db.commit()

async def save_subject_global_stat(subject: str, total: int, passed: int, pass_rate: float, 
                                  total_persons: int = 0, passed_persons: int = 0, person_pass_rate: float = 0.0):
    """Сохраняет глобальную статистику по конкретному предмету (включая количество людей)."""
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO subject_global_stats (subject, total_students, passed_students, pass_rate,
                                          total_persons, passed_persons, person_pass_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(subject) DO UPDATE SET
            total_students=excluded.total_students,
            passed_students=excluded.passed_students,
            pass_rate=excluded.pass_rate,
            total_persons=excluded.total_persons,
            passed_persons=excluded.passed_persons,
            person_pass_rate=excluded.person_pass_rate
    """, (subject, total, passed, pass_rate, total_persons, passed_persons, person_pass_rate))
    await db.commit()

async def save_cluster_subject_stat(cluster_id: int, subject: str, total: int, passed: int, pass_rate: float,
                                    total_persons: int = 0, passed_persons: int = 0, person_pass_rate: float = 0.0):
    """Сохраняет статистику предмета внутри кластера (включая количество людей)."""
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO cluster_subject_stats (cluster_id, subject, total_students, passed_students, pass_rate,
                                           total_persons, passed_persons, person_pass_rate)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cluster_id, subject) DO UPDATE SET
            total_students=excluded.total_students,
            passed_students=excluded.passed_students,
            pass_rate=excluded.pass_rate,
            total_persons=excluded.total_persons,
            passed_persons=excluded.passed_persons,
            person_pass_rate=excluded.person_pass_rate
    """, (cluster_id, subject, total, passed, pass_rate, total_persons, passed_persons, person_pass_rate))
    await db.commit()

async def get_cluster_subject_stats(cluster_id: int) -> dict:
    """Возвращает статистику по предметам для конкретного кластера. Формат: {subject: pass_rate}"""
    db = await get_db_connection()
    async with db.execute(
        "SELECT subject, pass_rate FROM cluster_subject_stats WHERE cluster_id = ?",
        (cluster_id,)
    ) as cursor:
        rows = await cursor.fetchall()
        return {r[0]: r[1] for r in rows}


async def get_subjects_with_stats() -> List[str]:
    """Возвращает список всех предметов, по которым есть статистика."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT subject FROM subject_global_stats WHERE total_students > 0 ORDER BY subject"
    ) as cursor:
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


async def get_global_subject_stats(subject: str) -> dict | None:
    """Глобальная статистика по одному предмету."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT total_students, passed_students, pass_rate, total_persons, passed_persons, person_pass_rate FROM subject_global_stats WHERE subject = ?",
        (subject,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "total": row[0],
            "passed": row[1],
            "pass_rate": row[2],
            "total_persons": row[3],
            "passed_persons": row[4],
            "person_pass_rate": row[5]
        }

async def get_teacher_subject_rank(teacher: str, subject: str) -> tuple[int, int] | None:
    """Возвращает место преподавателя в рейтинге по предмету (место, всего преподавателей)."""
    rating = await get_subject_rating(subject)
    if not rating:
        return None
    for i, entry in enumerate(rating, start=1):
        if entry["teacher"] == teacher:
            return i, len(rating)
    return None

async def get_last_parsed_num(enrollment_year: int, max_hours: int = 24) -> int:
    """
    Находит максимальный порядковый номер зачетки для года, 
    если записи обновлялись не позднее max_hours назад.
    """
    db = await get_db_connection()
    # SQL query to get the max record number suffix where last_updated is fresh
    # record_book format is YYYYNNNN (e.g. 20220150)
    query = """
        SELECT MAX(CAST(SUBSTR(record_book, 5) AS INTEGER))
        FROM rating_data
        WHERE enrollment_year = ? 
          AND last_updated >= datetime('now', ?)
    """
    async with db.execute(query, (enrollment_year, f"-{max_hours} hours")) as cursor:
        row = await cursor.fetchone()
        return row[0] if row and row[0] is not None else 0

async def get_records_count_by_year(enrollment_year: int) -> int:
    """
    Возвращает количество записей рейтинга для указанного года зачисления.
    Используется для оценки общего количества при парсинге.
    """
    db = await get_db_connection()
    query = "SELECT COUNT(*) FROM rating_data WHERE enrollment_year = ?"
    async with db.execute(query, (enrollment_year,)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else 0

# --- Данные для админ-панели (статистика по группам) ---

async def get_record_books_in_cluster(cluster_id: int) -> List[dict]:
    """Возвращает список зачеток в кластере (с их общим pass_rate)."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT record_book, pass_rate, total_subjects, passed_subjects FROM rating_data WHERE cluster_id = ? AND is_expelled = 0 ORDER BY record_book",
        (cluster_id,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [{"record_book": r[0], "pass_rate": r[1], "total_subjects": r[2], "passed_subjects": r[3]} for r in rows]

async def get_subject_status_in_cluster(cluster_id: int, subject: str) -> List[dict]:
    """Возвращает статусы зачеток кластера по конкретному предмету."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT record_book, subjects_json FROM rating_data WHERE cluster_id = ? AND is_expelled = 0 ORDER BY record_book",
        (cluster_id,)
    ) as cursor:
        rows = await cursor.fetchall()
        
    result = []
    for row in rows:
        rb = row[0]
        subj_json = row[1]
        if not subj_json:
            continue
        try:
            subjects = json.loads(subj_json)
            subj_data = next((s for s in subjects if s.get("subject") == subject), None)
            if subj_data:
                result.append({
                    "record_book": rb,
                    "status": subj_data.get("status", "Неизвестно"),
                    "mark": subj_data.get("mark", "Нет оценки")
                })
            else:
                result.append({
                    "record_book": rb,
                    "status": "Нет в профиле",
                    "mark": "-"
                })
        except json.JSONDecodeError:
            pass
            
    return result

async def get_record_book_subjects(record_book: str) -> List[dict]:
    """Возвращает список предметов и их статусы для зачетки."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT subjects_json FROM rating_data WHERE record_book = ?",
        (record_book,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row or not row[0]:
            return []
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            return []

