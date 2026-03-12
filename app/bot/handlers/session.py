from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
import re
from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton

from app.core.repositories.user import (
    get_record_book_number, save_record_book_number, get_user_settings, update_user_settings
)
from app.core.repositories.subject import (
    get_subject_note, save_subject_note
)
from app.core.repositories.rating import (
    get_student_cluster_info, get_rating_position
)
from app.bot.formatter import (
    get_course_from_semester, filter_results_by_settings, format_results, escape_md
)
from app.services.schedule_api import UsurtScraper
from app.bot.keyboards import (
    get_session_results_keyboard, get_settings_keyboard
)
from app.bot.states import SessionResults, NoteEdit, ChecklistAdd

router = Router()


async def show_results_view(target: Message | CallbackQuery, user_id: int, record_book_number: str):
    from app.core.repositories.subject import get_global_subject_stats, get_cluster_subject_stats
    from app.core.repositories.rating import get_rating_position, get_group_by_record_book
    from app.core.repositories.schedule import get_teachers_for_subject
    from app.core.database import get_db_connection
    msg = target if isinstance(target, Message) else target.message
    if isinstance(target, Message):
        msg = await target.answer(f"🔍 Ищу результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")
    else:
        await msg.edit_text(f"🔍 Ищу результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")

    settings = await get_user_settings(user_id)
    status, results_data = await UsurtScraper.get_session_results(record_book_number)
    
    if status == "NOT_FOUND":
        text = "❌ Зачетная книжка не найдена. Проверьте номер."
        await msg.edit_text(text, reply_markup=get_session_results_keyboard())
    elif status == "ERROR" or results_data is None:
        text = "❌ Ошибка при получении данных. Попробуйте позже."
        await msg.edit_text(text, reply_markup=get_session_results_keyboard())
    else:
        # Получаем рейтинговую информацию (если доступна)
        rating_info = {}
        cluster_pos = await get_rating_position(record_book_number, "cluster")
        if cluster_pos:
            rating_info["cluster_pos"] = cluster_pos
            
        year_pos = await get_rating_position(record_book_number, "year")
        if year_pos:
            rating_info["year_pos"] = year_pos
            
        all_pos = await get_rating_position(record_book_number, "all")
        if all_pos:
            rating_info["all_pos"] = all_pos
            
        if not rating_info:
            rating_info = None
            
        # We need cluster_id to fetch cluster subject stats
        cluster_id = None
        db = await get_db_connection()
        async with db.execute("SELECT cluster_id FROM rating_data WHERE record_book = ?", (record_book_number,)) as cur:
            row = await cur.fetchone()
            if row and row[0]:
                cluster_id = row[0]
                
        cluster_subject_stats = {}
        if cluster_id:
            cluster_subject_stats = await get_cluster_subject_stats(cluster_id)
            
        subject_stats = {}
        for item in results_data:
            subj_name = item.get("subject", "").strip()
            if subj_name and subj_name not in subject_stats:
                stats = await get_global_subject_stats(subj_name)
                if stats:
                    subject_stats[subj_name] = stats["pass_rate"]
        
        # Определяем группу студента и преподавателей
        teacher_map = {}
        student_group = await get_group_by_record_book(record_book_number)
        if student_group:
            seen_subjects = set()
            for item in results_data:
                subj_name = item.get("subject", "").strip()
                if subj_name and subj_name not in seen_subjects:
                    seen_subjects.add(subj_name)
                    teachers = await get_teachers_for_subject(student_group, subj_name)
                    if teachers:
                        teacher_map[subj_name] = teachers
        
        formatted_text = format_results(results_data, settings, rating_info, subject_stats, cluster_subject_stats, teacher_map)
        if len(formatted_text) > 4000:
            lines = formatted_text.split('\n')
            parts = []
            current_part = ""
            for line in lines:
                if len(current_part) + len(line) + 1 > 4000:
                    parts.append(current_part)
                    current_part = line
                else:
                    current_part += ("\n" + line) if current_part else line
            if current_part:
                parts.append(current_part)
                
            for i, part in enumerate(parts):
                markup = get_session_results_keyboard() if i == len(parts) - 1 else None
                if i == 0: await msg.edit_text(part, parse_mode="Markdown", reply_markup=markup)
                else: await msg.answer(part, parse_mode="Markdown", reply_markup=markup)
        else:
            await msg.edit_text(formatted_text, parse_mode="Markdown", reply_markup=get_session_results_keyboard())

@router.message(F.text == "📊 Мои результаты")
async def show_session_results(message: Message, state: FSMContext):
    record_book_number = await get_record_book_number(message.from_user.id)
    if not record_book_number:
        await message.answer("Для просмотра результатов мне нужно знать номер вашей зачетной книжки.\nПожалуйста, введите его (только цифры):")
        await state.set_state(SessionResults.waiting_for_record_book_number)
        return
    await show_results_view(message, message.from_user.id, record_book_number)

@router.message(SessionResults.waiting_for_record_book_number)
async def process_record_book_number(message: Message, state: FSMContext):
    number = message.text.strip()
    if not number.isdigit():
        await message.answer("⚠️ Номер зачетной книжки должен состоять только из цифр. Попробуйте еще раз.")
        return
    await save_record_book_number(
        message.from_user.id, 
        number, 
        username=message.from_user.username, 
        first_name=message.from_user.first_name
    )
    await state.clear()
    await show_results_view(message, message.from_user.id, number)

@router.callback_query(F.data == "change_record_book")
async def change_record_book_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Пожалуйста, введите новый номер зачетной книжки (только цифры):")
    await state.set_state(SessionResults.waiting_for_record_book_number)
    await callback.answer()

@router.callback_query(F.data == "refresh_results")
async def refresh_session_results(callback: CallbackQuery):
    record_book_number = await get_record_book_number(callback.from_user.id)
    if not record_book_number:
        await callback.answer("Номер зачетки не найден.")
        return
    await callback.message.edit_text(f"🔄 Обновляю результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")
    
    status, data = await UsurtScraper.get_session_results(record_book_number, use_cache=False)
    
    if status != "SUCCESS" or data is None:
        error_text = "❌ Зачетка не найдена." if status == "NOT_FOUND" else "❌ Ошибка сети."
        await callback.message.edit_text(error_text, reply_markup=get_session_results_keyboard())
    else:
        await show_results_view(callback, callback.from_user.id, record_book_number)
    await callback.answer()

@router.callback_query(F.data == "session_settings")
async def open_settings(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    await callback.message.edit_text(
        "⚙️ *Настройки отображения*\n\nВыберите, какие предметы нужно **СКРЫТЬ**:",
        reply_markup=get_settings_keyboard(settings),
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("toggle_setting:"))
async def toggle_setting(callback: CallbackQuery):
    key = callback.data.split(":")[1]
    user_id = callback.from_user.id
    settings = await get_user_settings(user_id)
    settings[key] = not settings.get(key, False)
    await update_user_settings(user_id, settings)
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(settings))
    await callback.answer("Настройка обновлена")

@router.callback_query(F.data == "back_to_results")
async def back_to_results(callback: CallbackQuery):
    record_book_number = await get_record_book_number(callback.from_user.id)
    if record_book_number:
        await show_results_view(callback, callback.from_user.id, record_book_number)
    else:
        await callback.message.edit_text("Ошибка: номер зачетки не найден.")

# --- Notes Handlers ---
@router.callback_query(F.data == "notes_root")
async def notes_root(callback: CallbackQuery):
    record_book_number = await get_record_book_number(callback.from_user.id)
    if not record_book_number:
        await callback.answer("Сначала получите результаты сессии.")
        return
    status, data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
    if status != "SUCCESS" or not data:
        await callback.answer("Нет данных. Обновите результаты.")
        return
    
    settings = await get_user_settings(callback.from_user.id)
    filtered_data = filter_results_by_settings(data, settings)
    
    if not filtered_data:
        await callback.answer("Все предметы скрыты фильтрами.")
        return

    def sem_sort_key(s):
        year_m = re.search(r'(\d{4})/\d{4}', s)
        year = int(year_m.group(1)) if year_m else 0
        sem_m = re.search(r'(\d+)\s*семестр', s)
        sem = int(sem_m.group(1)) if sem_m else 999
        return (year, sem)

    semesters = sorted(list(set(d['semester'] for d in filtered_data)), key=sem_sort_key)
    builder = InlineKeyboardBuilder()
    for sem in semesters:
        builder.button(text=sem, callback_data=f"notes_sem:{sem}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к результатам", callback_data="back_to_results"))
    await callback.message.edit_text("📂 Выберите семестр для заметок:", reply_markup=builder.as_markup())
    await callback.answer()

@router.callback_query(F.data.startswith("notes_sem:"))
async def notes_semester_select(callback: CallbackQuery):
    semester = callback.data.split(":", 1)[1]
    user_id = callback.from_user.id
    record_book_number = await get_record_book_number(user_id)
    status, data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
    
    settings = await get_user_settings(user_id)
    filtered_data = filter_results_by_settings(data, settings)
    subjects = sorted(list(set(d['subject'] for d in filtered_data if d['semester'] == semester and d['subject'].strip())))
    
    builder = InlineKeyboardBuilder()
    for i, subj in enumerate(subjects):
        builder.button(text=subj[:30], callback_data=f"notes_subj:{semester}:{i}")
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к семестрам", callback_data="notes_root"))
    
    await callback.message.edit_text(f"📂 Семестр: {semester}\nВыберите предмет:", reply_markup=builder.as_markup())
    await callback.answer()

async def show_subject_note_view(target: Message | CallbackQuery, user_id: int, subject_name: str, semester: str):
    note_data = await get_subject_note(user_id, subject_name)
    note_text = note_data.get("note_text", "")
    checklist = note_data.get("checklist", [])
    
    text = f"📝 *{subject_name}*\n\n"
    text += f"{note_text}\n\n" if note_text else "_Нет заметки_\n\n"
    
    if checklist:
        text += "*Чек-лист:*\n"
        for item in checklist:
            status = "✅" if item['done'] else "⬜"
            text += f"{status} {item['text']}\n"
    else:
        text += "_Чек-лист пуст_"
        
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ред. заметку", callback_data="note_edit_text")
    builder.button(text="➕ Пункт чек-листа", callback_data="note_add_item")
    
    for i, item in enumerate(checklist):
        status_icon = "✅" if item['done'] else "⬜"
        builder.button(text=f"{status_icon} {item['text'][:15]}...", callback_data=f"note_toggle:{i}")
        builder.button(text="🗑", callback_data=f"note_del:{i}")
    builder.adjust(2) # Edit, Add
    # Then 2 per row
    
    builder.row(InlineKeyboardButton(text=f"⬅️ Назад к предметам", callback_data=f"notes_sem:{semester}"))
    
    if isinstance(target, Message):
        await target.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@router.callback_query(F.data.startswith("notes_subj:"))
async def notes_subject_view(callback: CallbackQuery, state: FSMContext):
    try:
        _, semester, subj_idx_str = callback.data.split(":")
        subj_idx = int(subj_idx_str)
        user_id = callback.from_user.id
        record_book_number = await get_record_book_number(user_id)
        status, data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
        
        subjects = sorted(list(set(d['subject'] for d in data if d['semester'] == semester)))
        subject_name = subjects[subj_idx]
        
        await state.update_data(current_subject=subject_name, current_semester=semester)
        await show_subject_note_view(callback, user_id, subject_name, semester)
    except Exception:
        await callback.answer("Ошибка при открытии заметки.", show_alert=True)

@router.callback_query(F.data == "note_edit_text")
async def note_edit_text_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст заметки:")
    await state.set_state(NoteEdit.waiting_for_note_text)
    await callback.answer()

@router.message(NoteEdit.waiting_for_note_text)
async def note_edit_text_save(message: Message, state: FSMContext):
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    current_data = await get_subject_note(message.from_user.id, subject_name)
    await save_subject_note(message.from_user.id, subject_name, message.text, current_data.get("checklist", []))
    
    await state.set_state(None)
    await state.update_data(current_subject=subject_name, current_semester=semester)
    await show_subject_note_view(message, message.from_user.id, subject_name, semester)

@router.callback_query(F.data == "note_add_item")
async def note_add_item_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст пункта чек-листа:")
    await state.set_state(ChecklistAdd.waiting_for_item_text)
    await callback.answer()

@router.message(ChecklistAdd.waiting_for_item_text)
async def note_add_item_save(message: Message, state: FSMContext):
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    current_data = await get_subject_note(message.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    checklist.append({"text": message.text, "done": False})
    
    await save_subject_note(message.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
    
    await state.set_state(None)
    await state.update_data(current_subject=subject_name, current_semester=semester)
    await show_subject_note_view(message, message.from_user.id, subject_name, semester)

@router.callback_query(F.data.startswith("note_toggle:"))
async def note_toggle_item(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    current_data = await get_subject_note(callback.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    if 0 <= idx < len(checklist):
        checklist[idx]["done"] = not checklist[idx]["done"]
        await save_subject_note(callback.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
        await show_subject_note_view(callback, callback.from_user.id, subject_name, semester)
    await callback.answer()

@router.callback_query(F.data.startswith("note_del:"))
async def note_del_item(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    current_data = await get_subject_note(callback.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    if 0 <= idx < len(checklist):
        checklist.pop(idx)
        await save_subject_note(callback.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
        await show_subject_note_view(callback, callback.from_user.id, subject_name, semester)
    await callback.answer()
