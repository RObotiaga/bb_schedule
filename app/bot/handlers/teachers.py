from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from datetime import date, datetime, timedelta
import asyncio

from app.core.repositories.schedule import get_schedule_by_teacher
from app.core.repositories.user import get_record_book_number
from app.core.repositories.rating import get_student_cluster_info
from app.core.repositories.subject import (
    get_teacher_subject_rank,
    is_subscribed_to_teacher,
    subscribe_teacher, unsubscribe_teacher
)
from app.bot.keyboards import get_teacher_nav_keyboard, get_teacher_choices_keyboard, get_faculties_keyboard
from app.core.state import GlobalState

router = Router()

async def show_teacher_schedule(target: Message | CallbackQuery, teacher_name: str, day_offset: int):
    target_date = date.today() + timedelta(days=day_offset)
    date_str = target_date.strftime('%Y-%m-%d')
    
    lessons_raw = await get_schedule_by_teacher(teacher_name, date_str)
    
    merged_lessons = {}
    for lesson in lessons_raw:
        key = (lesson['time'], lesson['subject'], lesson['location'])
        if key not in merged_lessons:
            merged_lessons[key] = dict(lesson)
            merged_lessons[key]['groups'] = [lesson['group_name']]
        else:
            merged_lessons[key]['groups'].append(lesson['group_name'])
    lessons = list(merged_lessons.values())
    
    months = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    date_formatted = f"{weekdays[target_date.weekday()]} {target_date.day} {months[target_date.month - 1]}"
    
    if not lessons:
        week_number = target_date.isocalendar()[1]
        week_type = "Четная" if week_number % 2 == 0 else "Нечетная"
        header = f"*{week_type} неделя*\n*{teacher_name}*\n\n*{date_formatted}*"
        text = f"{header}\n❌Расписание отсутствует❌"
    else:
        week_type = lessons[0]['week_type'].capitalize()
        header = f"*{week_type} неделя*\n*{teacher_name}*\n\n*{date_formatted}*"
        lesson_parts = []
        for lesson in lessons:
            groups, group_prefix = lesson.get('groups', []), "с группой"
            if len(groups) > 1: group_prefix = "с группами"
            groups_str = ", ".join(sorted(list(set(groups))))
            part = f"⏰ {lesson['time']} {group_prefix} *{groups_str}*\n-  `{lesson['subject']}`\n-  `{lesson['location']}`"
            lesson_parts.append(part)
        text = f"{header}\n\n" + "\n\n".join(lesson_parts)
        
    user_id = target.from_user.id
    is_sub = await is_subscribed_to_teacher(user_id, teacher_name)
    keyboard = get_teacher_nav_keyboard(day_offset, is_sub)
    
    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        if target.message.text != text or target.message.reply_markup != keyboard: 
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await target.answer()

def is_teacher_match(query: str, teacher_name: str) -> bool:
    query = query.strip().lower()
    teacher_name = teacher_name.lower().replace('.', ' ').replace(',', ' ')
    
    if query in teacher_name:
        return True
        
    query_parts = query.split()
    teacher_parts = teacher_name.split()
    
    if not query_parts or not teacher_parts:
        return False
        
    last_name = query_parts[0]
    if last_name not in teacher_parts[0]:
        return False
        
    if len(query_parts) > 1 and len(teacher_parts) > 1:
        if not teacher_parts[1].startswith(query_parts[1][0]):
            return False
            
    if len(query_parts) > 2 and len(teacher_parts) > 2:
        if not teacher_parts[2].startswith(query_parts[2][0]):
            return False
            
    return True

@router.message(StateFilter(None), lambda message: message.text and 1 <= len(message.text.split()) <= 3 and not message.text.startswith("/") and message.text not in ["Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "📊 Мои результаты"])
async def process_teacher_search(message: types.Message, state: FSMContext):
    search_query = message.text.strip()
    
    matches = [t for t in GlobalState.ALL_TEACHERS_LIST if is_teacher_match(search_query, t)]
    
    if not matches:
        await message.reply("Преподаватель не найден. Проверьте правильность написания.")
        return
        
    if len(matches) == 1:
        # Found exact or single match
        await state.update_data(current_teacher=matches[0], day_offset=0)
        await show_teacher_schedule(message, matches[0], 0)
    else:
        # Multiple matches
        # Limit to 30 to avoid huge lists that hit Telegram's limits
        if len(matches) > 30:
             await message.reply(f"Найдено слишком много совпадений ({len(matches)}). Уточните запрос.")
             return
             
        await state.update_data(teacher_matches=matches)
        await message.reply("Выберите преподавателя:", reply_markup=get_teacher_choices_keyboard(matches))

@router.callback_query(F.data.startswith("teacher_select:"))
async def process_teacher_select(callback: CallbackQuery, state: FSMContext):
    try:
        idx = int(callback.data.split(":")[1])
        data = await state.get_data()
        matches = data.get("teacher_matches", [])
        
        # Fallback if state is lost, but usually keys are stable indices if list didn't change? 
        # Actually list comes from GlobalState which might change on reload, but indices in short term are fine.
        # Ideally we should encode name in callback if short enough or use a cache.
        # But here we used indices from the message generation context.
        # If state is lost, we can't recover easily without re-searching.
        
        if not matches:
             # Try to recover from GlobalState if we assume the list presented was from GlobalState?
             # But we filtered it.
             await callback.answer("Ошибка контекста. Повторите поиск.", show_alert=True)
             return

        if 0 <= idx < len(matches):
            teacher = matches[idx]
            await state.update_data(current_teacher=teacher, day_offset=0)
            await show_teacher_schedule(callback, teacher, 0)
        else:
            await callback.answer("Ошибка выбора.", show_alert=True)
            
    except Exception as e:
        await callback.answer(f"Ошибка: {e}", show_alert=True)

@router.callback_query(F.data.startswith("teacher_nav:"))
async def process_teacher_nav(callback: CallbackQuery, state: FSMContext):
    offset = int(callback.data.split(":")[1])
    data = await state.get_data()
    teacher = data.get("current_teacher")
    
    if teacher:
        await state.update_data(day_offset=offset)
        # Fix: message.edit_text is called inside show_teacher_schedule
        await show_teacher_schedule(callback, teacher, offset)
    else:
        await callback.answer("Не выбран преподаватель. Напишите фамилию заново.", show_alert=True)

@router.callback_query(F.data.startswith("teacher_sub:"))
async def process_teacher_subscription(callback: CallbackQuery, state: FSMContext):
    action = callback.data.split(":")[1]
    data = await state.get_data()
    teacher = data.get("current_teacher")
    day_offset = data.get("day_offset", 0)
    
    if not teacher:
        await callback.answer("Ошибка: преподаватель не выбран.", show_alert=True)
        return
        
    user_id = callback.from_user.id
    
    if action == "subscribe":
        await subscribe_teacher(user_id, teacher)
        await callback.answer(f"Вы подписались на {teacher}")
    elif action == "unsubscribe":
        await unsubscribe_teacher(user_id, teacher)
        await callback.answer(f"Вы отписались от {teacher}")
        
    # Refresh the view to update the keyboard
    await show_teacher_schedule(callback, teacher, day_offset)


