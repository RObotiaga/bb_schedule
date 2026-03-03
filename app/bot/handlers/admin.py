from aiogram import Router, F
from aiogram.types import Message
from app.bot.filters import IsAdmin
from app.bot.keyboards import admin_keyboard
from app.services.schedule_sync import run_full_sync
from app.services.rating_updater import run_rating_update
from app.core.state import GlobalState
from app.core.database import get_last_two_job_logs
import logging
import json

router = Router()

@router.message(IsAdmin(), F.text == "📊 Статус бота")
async def admin_bot_status(message: Message):
    def format_log(log):
        if not log:
            return "Нет данных"
        status_emoji = "✅" if log['status'] == 'SUCCESS' else "❌"
        duration = log['details'].get('duration_seconds', 0)
        time_str = log['end_time'].strftime("%d.%m.%Y %H:%M")
        
        details_txt = []
        for k, v in log['details'].items():
            if k == 'duration_seconds': continue
            details_txt.append(f"{k}: {v}")
        details_str = ", ".join(details_txt)
        
        return f"{status_emoji} *{time_str}* ({duration:.1f} сек)\n`{details_str}`"

    logs_sched = await get_last_two_job_logs("schedule_sync")
    logs_rating = await get_last_two_job_logs("rating_update")
    
    msg_parts = ["📊 **Статус фоновых задач**\n"]
    
    # Расписание
    msg_parts.append("📅 **Расписание:**")
    last_s = format_log(logs_sched[0]) if len(logs_sched) > 0 else "Нет данных"
    prev_s = format_log(logs_sched[1]) if len(logs_sched) > 1 else "Нет данных"
    msg_parts.append(f"Последнее: {last_s}")
    msg_parts.append(f"Предпоследнее: {prev_s}\n")
    
    # Рейтинг
    msg_parts.append("🏆 **Рейтинг:**")
    last_r = format_log(logs_rating[0]) if len(logs_rating) > 0 else "Нет данных"
    prev_r = format_log(logs_rating[1]) if len(logs_rating) > 1 else "Нет данных"
    msg_parts.append(f"Последнее: {last_r}")
    msg_parts.append(f"Предпоследнее: {prev_r}")
    
    await message.answer("\n".join(msg_parts), parse_mode="Markdown")


@router.message(IsAdmin(), F.text == "🔄 Обновить расписание")
async def admin_update_schedule(message: Message):
    await message.answer("🚀 Начинаю полное обновление (скачивание + парсинг)...")
    
    success = await run_full_sync()
    
    if success:
        await GlobalState.reload()
        await message.answer("✅ Обновление успешно завершено! Структура перезагружена.", reply_markup=admin_keyboard)
    else:
        await message.answer("❌ Обновление завершилось с ошибкой. Проверьте логи.", reply_markup=admin_keyboard)

@router.message(IsAdmin(), F.text == "📥 Перезагрузить структуру")
async def admin_reload_structure(message: Message):
    await message.answer("📥 Перезагружаю структуру из БД...")
    await GlobalState.reload()
    await message.answer("✅ Структура обновлена.", reply_markup=admin_keyboard)

@router.message(IsAdmin(), F.text == "🏆 Обновить рейтинг")
async def admin_update_rating(message: Message):
    await message.answer("🏆 Запускаю обновление рейтинга (парсинг зачёток + кластеризация)...\n"
                         "⏳ Это может занять некоторое время.")
    try:
        await run_rating_update()
        await message.answer("✅ Рейтинг успешно обновлён!", reply_markup=admin_keyboard)
    except Exception as e:
        logging.exception("Ошибка при обновлении рейтинга")
        await message.answer(f"❌ Ошибка при обновлении рейтинга: {e}", reply_markup=admin_keyboard)

@router.message(IsAdmin(), F.text == "⬅️ Выйти из админ-панели")
async def admin_exit(message: Message):
    from app.bot.keyboards import day_selection_keyboard
    await message.answer("Выход из админ-режима.", reply_markup=day_selection_keyboard)

@router.message(IsAdmin(), F.text == "/admin")
async def admin_panel(message: Message):
    await message.answer("Админ-панель:", reply_markup=admin_keyboard)
