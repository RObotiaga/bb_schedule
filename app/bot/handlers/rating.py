"""Хендлер команды /top — рейтинг группы студента."""
from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command

from app.core.config import ADMIN_ID
from app.core.database import (
    get_record_book_number,
    get_student_cluster_info,
    get_top_students,
    get_users_by_record_book,
)

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message):
    record_book = await get_record_book_number(message.from_user.id)
    if not record_book:
        await message.answer(
            "❌ Сначала привяжи номер зачётки: нажми <b>📊 Мои результаты</b>.",
            parse_mode="HTML",
        )
        return

    info = await get_student_cluster_info(record_book)
    if not info:
        await message.answer(
            "📭 Данных рейтинга пока нет.\n"
            "Рейтинг обновляется автоматически раз в сутки.\n"
            "Попробуй позже.",
        )
        return

    if info["is_expelled"]:
        await message.answer("⚠️ Эта зачётка определена как отчисленная.")
        return

    is_admin = message.from_user.id == ADMIN_ID
    cluster_id = info["cluster_id"]

    # Получаем всех студентов кластера, отсортированных по pass_rate
    students = await get_top_students(scope="cluster", scope_value=cluster_id, limit=100)

    if not students:
        await message.answer("📭 Нет данных по группе.")
        return

    lines = [f"🏆 <b>Рейтинг группы</b> (кластер #{cluster_id})\n"]
    for pos, student in enumerate(students, start=1):
        rb = student["record_book"]
        passed = student["passed"]
        total = student["total"]
        rate = student["pass_rate"]

        entry = f"{pos}. {rb} — {passed}/{total} ({rate:.1f}%)"

        # Для админа добавляем ссылки на телеграм-пользователей с этой зачёткой
        if is_admin:
            user_dicts = await get_users_by_record_book(rb)
            if user_dicts:
                links = []
                for u in user_dicts:
                    uid = u["user_id"]
                    username = u["username"]
                    first_name = u["first_name"]
                    
                    if username:
                        text = f"@{username}"
                    elif first_name:
                        text = str(first_name)
                    else:
                        text = "Профиль"
                        
                    links.append(f'<a href="tg://user?id={uid}">{text}</a>')
                
                user_links = " ".join(links)
                entry += f" {user_links}"

        # Выделяем строку текущего пользователя жирным
        if rb == record_book:
            entry = f"<b>{entry} ← ты</b>"

        lines.append(entry)

    await message.answer("\n".join(lines), parse_mode="HTML")
