from aiogram import Router, F
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from app.bot.filters import IsAdmin
from app.bot.keyboards import admin_keyboard
from app.services.schedule_sync import run_full_sync
from app.services.rating_updater import run_rating_update
from app.core.state import GlobalState
from app.core.database import get_last_two_job_logs
from app.services.db_transfer import export_rating_data, import_rating_data
import logging
import json
import os

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
    status_msg = await message.answer("🏆 Запускаю обновление рейтинга (парсинг зачёток + кластеризация)...\n"
                         "⏳ Это может занять некоторое время.")
    try:
        await run_rating_update(bot=message.bot, status_message=status_msg)
        await message.answer("✅ Рейтинг успешно обновлён!", reply_markup=admin_keyboard)
    except Exception as e:
        logging.exception("Ошибка при обвалвлении рейтинга")
        await message.answer(f"❌ Ошибка при обновлении рейтинга: {e}", reply_markup=admin_keyboard)

import gzip

@router.message(IsAdmin(), F.text == "📤 Экспорт рейтинга")
async def admin_export_rating(message: Message):
    await message.answer("📤 Подготавливаю экспорт рейтинга...")
    try:
        json_data = await export_rating_data()
        compressed_data = gzip.compress(json_data.encode("utf-8"))
        file = BufferedInputFile(compressed_data, filename="rating_export.json.gz")
        await message.answer_document(file, caption="✅ Экспорт рейтинга завершен (сжат gzip).")
    except Exception as e:
        logging.exception("Ошибка при экспорте рейтинга")
        await message.answer(f"❌ Ошибка при экспорте: {e}")

@router.message(IsAdmin(), F.text == "📥 Импорт рейтинга")
async def admin_import_rating_start(message: Message):
    await message.answer("📥 Пожалуйста, отправьте файл (.json или .json.gz) с данными рейтинга.")

@router.message(IsAdmin(), F.document)
async def admin_import_rating_file(message: Message):
    filename = message.document.file_name or ""
    if not (filename.endswith(".json") or filename.endswith(".gz")):
        return

    status_msg = await message.answer("📥 Обработка файла...")
    try:
        file_info = await message.bot.get_file(message.document.file_id)
        file_content = await message.bot.download_file(file_info.file_path)
        raw_data = file_content.read()
        
        if filename.endswith(".gz"):
            raw_data = gzip.decompress(raw_data)
            
        json_data = raw_data.decode("utf-8")
        
        success = await import_rating_data(json_data)
        if success:
            await status_msg.edit_text("✅ Данные рейтинга успешно импортированы!")
        else:
            await status_msg.edit_text("❌ Ошибка при импорте данных. Проверьте формат файла.")
    except Exception as e:
        logging.exception("Ошибка при импорте рейтинга")
        await status_msg.edit_text(f"❌ Ошибка при импорте: {e}")

@router.message(IsAdmin(), F.text == "📉 Статистика отчислений")
async def admin_expelled_statistics(message: Message):
    from app.core.database import get_expelled_statistics
    
    try:
        stats = await get_expelled_statistics()
        
        msg_parts = [
            "📉 *Статистика отчислений:*\n",
            f"🔹 С начала учебного года (с 01.09): {stats['since_year_start']}",
            f"🔹 С начала семестра: {stats['since_semester_start']}",
            f"🔹 Всего отчисленных в базе: {stats['total']}"
        ]
        
        record_books = stats.get('all_record_books', [])
        
        text = "\n".join(msg_parts)
        if not record_books:
            text += "\n\nСписок отчисленных пуст."
            await message.answer(text, parse_mode="Markdown")
        else:
            list_text = ", ".join(f"`{rb}`" for rb in record_books)
            # Если текст со списком помещается в лимит Telegram (4096), отправляем прямо в сообщении
            if len(text) + len(list_text) < 3800:
                text += "\n\n📋 *Список зачеток:*\n" + list_text
                await message.answer(text, parse_mode="Markdown")
            else:
                text += "\n\n📋 Список зачеток прикреплен файлом (слишком длинный для сообщения)."
                await message.answer(text, parse_mode="Markdown")
                
                from aiogram.types import BufferedInputFile
                file_content = "\n".join(record_books).encode('utf-8')
                doc = BufferedInputFile(file_content, filename="expelled_students.txt")
                await message.answer_document(doc, caption="Список зачеток отчисленных")
    except Exception as e:
        logging.exception("Ошибка получения статистики отчислений")
        await message.answer(f"❌ Возникла ошибка: {e}")

@router.message(IsAdmin(), F.text == "⬅️ Выйти из админ-панели")
async def admin_exit(message: Message):
    from app.bot.keyboards import day_selection_keyboard
    await message.answer("Выход из админ-режима.", reply_markup=day_selection_keyboard)

@router.message(IsAdmin(), F.text == "/admin")
async def admin_panel(message: Message):
    await message.answer("Админ-панель:", reply_markup=admin_keyboard)

# --- Статистика по группам ---

from app.core.database import (
    get_all_cluster_groups, get_cluster_by_group, get_group_by_cluster, get_cluster_subjects, 
    get_subject_status_in_cluster, get_record_books_in_cluster, get_record_book_subjects
)
from app.bot.keyboards import (
    get_admin_faculties_keyboard, get_admin_courses_keyboard, get_admin_groups_keyboard,
    get_admin_group_actions_keyboard, get_admin_group_subjects_keyboard, get_admin_group_record_books_keyboard,
    AdminCourseCallbackFactory
)
from app.core.state import GlobalState

@router.message(IsAdmin(), F.text == "👥 Группы")
async def admin_groups_list(message: Message):
    if not GlobalState.FACULTIES_LIST:
        await message.answer("Структура факультетов не загружена.")
        return
    kb = get_admin_faculties_keyboard(GlobalState.FACULTIES_LIST)
    await message.answer("Выберите факультет:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data == "adm_back_fac")
async def admin_groups_back_fac(callback: CallbackQuery):
    kb = get_admin_faculties_keyboard(GlobalState.FACULTIES_LIST)
    await callback.message.edit_text("Выберите факультет:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_fac:"))
async def admin_groups_select_faculty(callback: CallbackQuery):
    faculty_id = int(callback.data.split(":")[1])
    kb = get_admin_courses_keyboard(faculty_id, GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA)
    if kb:
        await callback.message.edit_text("Выберите курс:", reply_markup=kb)
    else:
        await callback.answer("Ошибка: факультет не найден")

@router.callback_query(IsAdmin(), F.data.startswith("adm_back_crs:"))
async def admin_groups_back_crs(callback: CallbackQuery):
    faculty_id = int(callback.data.split(":")[1])
    kb = get_admin_courses_keyboard(faculty_id, GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA)
    if kb:
        await callback.message.edit_text("Выберите курс:", reply_markup=kb)

@router.callback_query(IsAdmin(), AdminCourseCallbackFactory.filter())
async def admin_groups_select_course(callback: CallbackQuery, callback_data: AdminCourseCallbackFactory):
    course_id = callback_data.course_id
    faculty_id = callback_data.faculty_id
    
    try:
        faculty = GlobalState.FACULTIES_LIST[faculty_id]
        kb = get_admin_groups_keyboard(faculty, str(course_id), GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA)
        await callback.message.edit_text("Выберите группу:", reply_markup=kb)
    except Exception as e:
        await callback.answer("Ошибка при выборе курса")

@router.callback_query(IsAdmin(), F.data.startswith("adm_grp_name:"))
async def admin_groups_select_group(callback: CallbackQuery):
    group_name = callback.data.split(":", 1)[1]
    cluster_id = await get_cluster_by_group(group_name)
    if cluster_id is None:
        await callback.answer("У этой группы еще нет собранной статистики.", show_alert=True)
        return
    kb = get_admin_group_actions_keyboard(cluster_id)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите действие:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_grp:"))
async def admin_group_actions(callback: CallbackQuery):
    cluster_id = int(callback.data.split(":")[1])
    group_name = await get_group_by_cluster(cluster_id)
    kb = get_admin_group_actions_keyboard(cluster_id)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите действие:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_act_subj:"))
async def admin_group_subjects_list(callback: CallbackQuery):
    cluster_id = int(callback.data.split(":")[1])
    group_name = await get_group_by_cluster(cluster_id)
    subjects = sorted(list(await get_cluster_subjects(cluster_id)))
    kb = get_admin_group_subjects_keyboard(cluster_id, subjects, page=0)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите предмет:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_subj_page:"))
async def admin_group_subjects_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    cluster_id = int(parts[1])
    page = int(parts[2])
    group_name = await get_group_by_cluster(cluster_id)
    subjects = sorted(list(await get_cluster_subjects(cluster_id)))
    kb = get_admin_group_subjects_keyboard(cluster_id, subjects, page=page)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите предмет:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_subj:"))
async def admin_group_subject_status(callback: CallbackQuery):
    parts = callback.data.split(":")
    cluster_id = int(parts[1])
    subj_idx = int(parts[2])
    
    group_name = await get_group_by_cluster(cluster_id)
    subjects = sorted(list(await get_cluster_subjects(cluster_id)))
    
    if subj_idx >= len(subjects) or subj_idx < 0:
         await callback.answer("Ошибка: предмет не найден")
         return
         
    subject = subjects[subj_idx]
    statuses = await get_subject_status_in_cluster(cluster_id, subject)
    
    lines = [f"📊 *Статусы по {subject} ({group_name}):*"]
    for s in statuses:
        lines.append(f"• `{s['record_book']}`: {s['status']} ({s['mark']})")
        
    text = "\n".join(lines)
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    back_kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="⬅️ Назад к предметам", callback_data=f"adm_g_act_subj:{cluster_id}")).as_markup()
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (слишком длинно)"
        
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_act_rec:"))
async def admin_group_record_books_list(callback: CallbackQuery):
    cluster_id = int(callback.data.split(":")[1])
    group_name = await get_group_by_cluster(cluster_id)
    record_books = await get_record_books_in_cluster(cluster_id)
    
    kb = get_admin_group_record_books_keyboard(cluster_id, record_books, page=0)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите зачетку:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_rec_page:"))
async def admin_group_record_books_page(callback: CallbackQuery):
    parts = callback.data.split(":")
    cluster_id = int(parts[1])
    page = int(parts[2])
    group_name = await get_group_by_cluster(cluster_id)
    record_books = await get_record_books_in_cluster(cluster_id)
    
    kb = get_admin_group_record_books_keyboard(cluster_id, record_books, page=page)
    await callback.message.edit_text(f"Группа: {group_name}\nВыберите зачетку:", reply_markup=kb)

@router.callback_query(IsAdmin(), F.data.startswith("adm_g_rec:"))
async def admin_group_record_book_status(callback: CallbackQuery):
    parts = callback.data.split(":")
    cluster_id = int(parts[1])
    rec_idx = int(parts[2])
    
    group_name = await get_group_by_cluster(cluster_id)
    record_books = await get_record_books_in_cluster(cluster_id)
    
    if rec_idx >= len(record_books) or rec_idx < 0:
         await callback.answer("Ошибка: зачетка не найдена")
         return
         
    rb_data = record_books[rec_idx]
    rb_num = rb_data['record_book']
    subjects = await get_record_book_subjects(rb_num)
    
    lines = [f"🧾 *Зачетка {rb_num} ({group_name}):*"]
    for s in subjects:
        subj_name = s.get('subject', 'Неизвестно')
        status = s.get('status', 'Неизвестно')
        mark = s.get('mark', '-')
        lines.append(f"• {subj_name}: {status} ({mark})")
        
    text = "\n".join(lines)
    from aiogram.types import InlineKeyboardButton
    from aiogram.utils.keyboard import InlineKeyboardBuilder
    back_kb = InlineKeyboardBuilder().row(InlineKeyboardButton(text="⬅️ Назад к зачеткам", callback_data=f"adm_g_act_rec:{cluster_id}")).as_markup()
    
    if len(text) > 4000:
        text = text[:4000] + "\n... (слишком длинно)"
        
    await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=back_kb)
