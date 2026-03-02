"""Хендлер команды /top — рейтинг студента."""
from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from app.core.database import (
    get_record_book_number,
    get_student_cluster_info,
    get_rating_position,
    get_cluster_size,
)

router = Router()


@router.message(Command("top"))
async def cmd_top(message: Message):
    record_book = await get_record_book_number(message.from_user.id)
    if not record_book:
        await message.answer(
            "❌ Сначала привяжи номер зачётки: нажми *📊 Мои результаты*.",
            parse_mode="Markdown",
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

    lines = [
        "🏆 *Твой рейтинг*\n",
        f"📊 Закрыто предметов: {info['passed_subjects']}/{info['total_subjects']} "
        f"({info['pass_rate']:.1f}%)\n",
    ]

    # Рейтинг по специальности (кластеру)
    cluster_pos = await get_rating_position(record_book, "cluster")
    if cluster_pos and info["cluster_id"]:
        cluster_size = await get_cluster_size(info["cluster_id"])
        lines.append(
            f"🎯 *Среди специальности* (кластер #{info['cluster_id']}, ~{cluster_size} чел.):\n"
            f"   Место: *{cluster_pos[0]}* из {cluster_pos[1]}"
        )

    # Рейтинг по году зачисления
    year_pos = await get_rating_position(record_book, "year")
    if year_pos:
        lines.append(
            f"\n🎓 *Среди {info['enrollment_year']} года* (не отчислены):\n"
            f"   Место: *{year_pos[0]}* из {year_pos[1]}"
        )

    # Общий рейтинг
    all_pos = await get_rating_position(record_book, "all")
    if all_pos:
        lines.append(
            f"\n🌍 *Среди всех неотчисленных*:\n"
            f"   Место: *{all_pos[0]}* из {all_pos[1]}"
        )

    await message.answer("\n".join(lines), parse_mode="Markdown")
