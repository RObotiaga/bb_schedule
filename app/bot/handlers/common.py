from aiogram import Router, F, types
from aiogram.filters import CommandStart
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext

from app.core.database import get_user_group_db, save_user_group_db
from app.bot.keyboards import (
    day_selection_keyboard, get_faculties_keyboard, 
    get_courses_keyboard, get_groups_keyboard, CourseCallbackFactory,
    get_welcome_inline_keyboard
)
from app.core.state import GlobalState

router = Router()

@router.message(CommandStart())
async def send_welcome(message: Message):
    user_group = await get_user_group_db(message.from_user.id)
    
    if user_group:
        await message.answer(
            f"👋 С возвращением! Ваша группа: *{user_group}*.",
            reply_markup=get_welcome_inline_keyboard(),
            parse_mode="Markdown"
        )
        await message.answer(
            "Вы можете посмотреть расписание на выбранный день.",
            reply_markup=day_selection_keyboard,
            parse_mode="Markdown"
        )
    else:
        await save_user_group_db(message.from_user.id, None)
        await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\n"
                             "Для поиска по группе - выберите ваш факультет.\n"
                             "Для поиска по преподавателю - просто напишите его фамилию.",
                             reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST))

@router.callback_query(F.data == "change_group")
async def change_group_callback(callback: CallbackQuery):
    await callback.message.edit_text(
        "Пожалуйста, выберите ваш факультет:", 
        reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST)
    )
    await callback.answer()

@router.callback_query(F.data.startswith("faculty:"))
async def process_faculty_choice(callback: CallbackQuery):
    parts = callback.data.split(":")
    faculty_id = int(parts[1])
    faculty_name = GlobalState.FACULTIES_LIST[faculty_id] 
    
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", 
        reply_markup=get_courses_keyboard(faculty_id, GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(CourseCallbackFactory.filter())
async def process_course_choice_factory(callback: CallbackQuery, callback_data: CourseCallbackFactory):
    faculty_name = GlobalState.FACULTIES_LIST[callback_data.faculty_id]
    course_name = str(callback_data.course_id)
    
    await callback.message.edit_text(
        f"Факультет: *{faculty_name}*, Курс: *{course_name}*.\n\nВыберите вашу группу:", 
        reply_markup=get_groups_keyboard(faculty_name, course_name, GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA), 
        parse_mode="Markdown"
    )
    await callback.answer()

@router.callback_query(F.data.startswith("group:"))
async def process_group_choice(callback: CallbackQuery):
    group = callback.data.split(":")[1]
    await save_user_group_db(callback.from_user.id, group)
    await callback.message.delete()
    await callback.message.answer(f"Отлично! Ваша группа *{group}* сохранена.", reply_markup=day_selection_keyboard, parse_mode="Markdown")
    await callback.answer()

@router.callback_query(F.data == "back_to_faculties")
async def back_to_faculties(callback: CallbackQuery):
    await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST))
    await callback.answer()

@router.callback_query(F.data.startswith("back_to_courses:"))
async def back_to_courses(callback: CallbackQuery):
    try:
        faculty_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации.", show_alert=True)
        await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard(GlobalState.FACULTIES_LIST))
        return

    faculty_name = GlobalState.FACULTIES_LIST[faculty_id]
    
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", 
        reply_markup=get_courses_keyboard(faculty_id, GlobalState.FACULTIES_LIST, GlobalState.STRUCTURED_DATA), 
        parse_mode="Markdown"
    )
    await callback.answer()
