import json
import logging
import re
from typing import List, Dict, Any, Tuple
import aiosqlite
from app.core.database import get_db_connection

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

async def get_teachers_for_subject(group_name: str, subject: str) -> List[str]:
    """
    Возвращает список уникальных преподавателей для предмета в группе.
    Сначала ищет в текущем расписании (schedule), затем, если не найдено,
    использует teacher_stats как запасной источник (исторические данные).
    """
    db = await get_db_connection()
    # Убираем " (Nп/г)" для поиска базового предмета
    base_subject = re.sub(r'\s*\(\d+\s*п/г\)', '', subject).strip()

    # 1. Ищем в текущем расписании
    async with db.execute("""
        SELECT DISTINCT teacher FROM schedule
        WHERE group_name = ?
          AND (subject = ? OR subject = ? OR subject LIKE ?)
          AND teacher IS NOT NULL AND teacher != 'Не указан'
    """, (group_name, subject, base_subject, base_subject + ' (%п/г)')) as cursor:
        rows = await cursor.fetchall()
        if rows:
            return [r[0] for r in rows]

    # 2. Запасной вариант: teacher_stats (данные прошлых семестров)
    async with db.execute("""
        SELECT DISTINCT teacher FROM teacher_stats
        WHERE group_name = ?
          AND (subject = ? OR subject = ? OR subject LIKE ?)
    """, (group_name, subject, base_subject, base_subject + ' (%п/г)')) as cursor:
        rows = await cursor.fetchall()
        return [r[0] for r in rows]


