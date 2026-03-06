from aiogram import Router, F, types
from aiogram.types import Message, CallbackQuery
from datetime import date, datetime, timedelta
from typing import List

from app.core.database import get_user_group_db, get_schedule_by_group, get_subscribed_teachers, get_schedule_by_teacher
from app.bot.keyboards import get_faculties_keyboard

router = Router()

def format_schedule_message(group: str, target_date: date, lessons: List[dict]) -> str:
    months = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    date_str = f"{weekdays[target_date.weekday()]} {target_date.day} {months[target_date.month - 1]}"
    
    if not lessons:
        week_number = target_date.isocalendar()[1]
        week_type = "Четная" if week_number % 2 == 0 else "Нечетная"
        header = f"*{week_type} неделя*\n*{group}*\n\n*{date_str}*"
        return f"{header}\n❌Расписание отсутствует❌"
        
    week_type = lessons[0]['week_type'].capitalize()
    if 'сессия' in week_type.lower():
        header = f"*{week_type}*\n*{group}*\n\n*{date_str}*"
    else:
        header = f"*{week_type} неделя*\n*{group}*\n\n*{date_str}*"
    
    lesson_parts = []
    for lesson in lessons:
        if lesson.get('is_subscription'):
            part = f"🔔 *[Подписка]* *{lesson['time']}*\n-  *{lesson['subject']}*\n-  *{lesson['teacher']}*\n-  *{lesson['location']}*"
        else:
            part = f"⏰ {lesson['time']}\n-  `{lesson['subject']}`\n-  `{lesson['teacher']}`\n-  `{lesson['location']}`"
        lesson_parts.append(part)
        
    return f"{header}\n\n" + "\n\n".join(lesson_parts)

async def show_schedule(target: Message | CallbackQuery, group: str, day_offset: int, user_id: int):
    target_date = date.today() + timedelta(days=day_offset)
    date_str = target_date.strftime("%Y-%m-%d")
    
    base_lessons = await get_schedule_by_group(group, date_str)
    all_lessons = [dict(lesson) for lesson in base_lessons]
    
    # Fetch subscriptions
    subscribed_teachers = await get_subscribed_teachers(user_id)
    for teacher in subscribed_teachers:
        teacher_lessons = await get_schedule_by_teacher(teacher, date_str)
        for t_lesson in teacher_lessons:
            lesson_dict = dict(t_lesson)
            lesson_dict['is_subscription'] = True
            all_lessons.append(lesson_dict)
            
    # Deduplicate lessons based on time, subject, teacher, location
    # Prioritizes marking existing lessons as subscriptions
    unique_lessons = {}
    for lesson in all_lessons:
        key = (lesson['time'], lesson['subject'], lesson['teacher'], lesson['location'])
        if key not in unique_lessons or lesson.get('is_subscription'):
            unique_lessons[key] = lesson
            
    final_lessons = list(unique_lessons.values())
    
    # Sort all lessons by time
    final_lessons.sort(key=lambda x: x['time'])
    
    text = format_schedule_message(group, target_date, final_lessons)
    
    if isinstance(target, Message):
        await target.answer(text, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        await target.message.edit_text(text, parse_mode="Markdown")
        await target.answer()

@router.message(F.text.in_(["Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"]))
async def day_button_handler(message: Message):
    user_group = await get_user_group_db(message.from_user.id)
    
    if not user_group:
        from app.core.state import GlobalState # Import here to avoid circular dependency if any (though state is standalone)
        await message.answer(
            "ℹ️ Сначала выберите вашу группу.",
            reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST)
        )
        return
    
    today_weekday = datetime.now().weekday()
    
    if message.text == "Сегодня":
        day_offset = 0
    elif message.text == "Завтра":
        day_offset = 1
    else:
        days_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5}
        target_weekday = days_map.get(message.text, 0)
        day_offset = (target_weekday - today_weekday) % 7
    
    await show_schedule(message, user_group, day_offset, message.from_user.id)

