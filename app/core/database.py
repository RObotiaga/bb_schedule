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
            group_name TEXT PRIMARY KEY,
            cluster_id INTEGER NOT NULL,
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
            status TEXT,          -- 'SUCCESS' или 'ERROR'
            details_json TEXT     -- JSON с дополнительной информацией 
        )
    """)
    
    await db.execute("""
        CREATE TABLE IF NOT EXISTS teacher_stats (
            teacher TEXT,
            subject TEXT,
            group_name TEXT,
            total_students INTEGER,
            passed_students INTEGER,
            pass_rate REAL,
            academic_year TEXT,
            UNIQUE(teacher, subject)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_job_logs_name ON job_logs (job_name)")

    await db.commit()


