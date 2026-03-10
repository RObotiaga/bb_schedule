import json
from typing import List, Tuple
from app.core.database import get_db_connection

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
