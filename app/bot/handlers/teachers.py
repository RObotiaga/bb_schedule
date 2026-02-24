from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.filters import StateFilter
from datetime import date, datetime, timedelta
import asyncio

from app.core.database import get_schedule_by_teacher
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
        
    keyboard = get_teacher_nav_keyboard(day_offset)
    
    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        if target.message.text != text: 
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await target.answer()

@router.message(StateFilter(None), lambda message: message.text and len(message.text.split()) == 1 and message.text not in ["Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "/start", "📊 Мои результаты"])
async def process_teacher_search(message: types.Message, state: FSMContext):
    # Simple heuristic: if it's a single word and not a command/button, treat as teacher surname
    search_query = message.text.strip().lower()
    
    matches = [t for t in GlobalState.ALL_TEACHERS_LIST if search_query in t.lower()]
    
    if not matches:
        await message.reply("Преподаватель не найден. Попробуйте еще раз или выберите факультет:", reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST))
        return
        
    if len(matches) == 1:
        # Found exact or single match
        await state.update_data(current_teacher=matches[0], day_offset=0)
        await show_teacher_schedule(message, matches[0], 0)
    else:
        # Multiple matches
        # Limit to 5-10 to avoid huge lists
        if len(matches) > 10:
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
