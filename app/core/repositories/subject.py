import json
from typing import List, Tuple
from app.core.database import get_db_connection


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
            "total_subjects": row[0], # Parity with old bug? Wait no, just returning keys
            "passed_subjects": row[1],
            "total_persons": row[3],
            "passed_persons": row[4],
            "person_pass_rate": row[5]
        }

async def get_teacher_subject_rank(teacher: str, subject: str) -> tuple[int, int] | None:
    """Возвращает место преподавателя в рейтинге по предмету (место, всего преподавателей)."""
    # This call to get_subject_rating was buggy in original database.py.
    # We will try to import it, if we find it in next step we'll fix the import.
    try:
        from app.services.subject_stats import get_subject_rating
        rating = await get_subject_rating(subject)
        if not rating:
            return None
        for i, entry in enumerate(rating, start=1):
            if entry["teacher"] == teacher:
                return i, len(rating)
    except Exception:
        pass
    return None

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
                is_passed = subj_data.get("passed", False)
                grade = subj_data.get("grade", "Нет оценки")
                result.append({
                    "record_book": rb,
                    "status": "✅ Сдано" if is_passed else "❌ Не сдано",
                    "mark": grade
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
