import json
from datetime import datetime
from typing import List, Dict, Tuple
from app.core.database import get_db_connection

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

async def save_cluster_group(cluster_id: int, group_name: str, similarity: float):
    """Сохраняет связь кластера с группой расписания."""
    db = await get_db_connection()
    await db.execute(
        "INSERT OR REPLACE INTO cluster_groups (group_name, cluster_id, similarity) VALUES (?, ?, ?)",
        (group_name, cluster_id, similarity),
    )
    await db.commit()

async def get_group_by_cluster(cluster_id: int) -> str | None:
    """Возвращает имя группы по cluster_id."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT group_name FROM cluster_groups WHERE cluster_id = ? LIMIT 1",
        (cluster_id,),
    ) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else None

async def get_group_by_record_book(record_book: str) -> str | None:
    """record_book → cluster_id → group_name (через JOIN)."""
    db = await get_db_connection()
    async with db.execute("""
        SELECT cg.group_name 
        FROM rating_data rd
        JOIN cluster_groups cg ON rd.cluster_id = cg.cluster_id
        WHERE rd.record_book = ? AND rd.is_expelled = 0
    """, (record_book,)) as cursor:
        row = await cursor.fetchone()
        return row[0] if row else None

async def get_cluster_by_group(group_name: str) -> int | None:
    """Возвращает cluster_id по имени группы (регистронезависимо)."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT cluster_id FROM cluster_groups WHERE LOWER(group_name) = LOWER(?)",
        (group_name,),
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

async def get_schedule_groups_subjects() -> Dict[str, dict]:
    """Возвращает {group_name: {'course': int, 'subjects': {subjects...}}} из расписания."""
    db = await get_db_connection()
    async with db.execute(
        "SELECT DISTINCT group_name, course, subject FROM schedule WHERE subject IS NOT NULL AND course IS NOT NULL"
    ) as cursor:
        rows = await cursor.fetchall()
        result: Dict[str, dict] = {}
        for row in rows:
            group = row[0]
            try:
                course = int(row[1])
            except (ValueError, TypeError):
                continue
            if group not in result:
                result[group] = {"course": course, "subjects": set()}
            result[group]["subjects"].add(row[2])
        return result

async def get_last_parsed_num(enrollment_year: int, max_hours: int = 24) -> int:
    """
    Находит максимальный порядковый номер зачетки для года, 
    если записи обновлялись не позднее max_hours назад.
    """
    db = await get_db_connection()
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
