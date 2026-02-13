from aiogram import Router, F
from aiogram.types import Message
from app.bot.filters import IsAdmin
from app.bot.keyboards import admin_keyboard
from app.services.schedule_sync import run_full_sync
from app.core.state import GlobalState
import logging

router = Router()

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

@router.message(IsAdmin(), F.text == "⬅️ Выйти из админ-панели")
async def admin_exit(message: Message):
    from app.bot.keyboards import day_selection_keyboard
    await message.answer("Выход из админ-режима.", reply_markup=day_selection_keyboard)

@router.message(IsAdmin(), F.text == "/admin")
async def admin_panel(message: Message):
    await message.answer("Админ-панель:", reply_markup=admin_keyboard)
