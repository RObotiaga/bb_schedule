# FILE: bot.py
import asyncio
import logging
import os
import sys
import pytz
import traceback
from datetime import date, timedelta
from typing import List, Optional
import re
import sqlite3

# --- ДОБАВЛЕНИЕ: Используем decouple для получения переменных ---
from decouple import config
# -------------------------------------------------------------

# --- ДОБАВЛЕНИЕ ДЛЯ ПЛАНИРОВАНИЯ ---
from apscheduler.schedulers.asyncio import AsyncIOScheduler
# ----------------------------------

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, BaseFilter, Command   
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from aiogram.types.error_event import ErrorEvent

# --- НОВЫЕ ИМПОРТЫ ---
from config import DB_PATH 
from database import (
    initialize_database, load_structure_from_db, 
    save_user_group_db, get_user_group_db, get_all_user_ids, get_all_courses,
    log_broadcast, get_last_broadcast, delete_last_broadcast_log,
    get_schedule_by_group, get_schedule_by_teacher,
    save_record_book_number, get_record_book_number,
    get_user_settings, update_user_settings
)
from usurt_scraper import UsurtScraper

# --- КОНФИГУРАЦИЯ (УНИФИКАЦИЯ ПУТЕЙ) ---
# Используем config, но с дефолтом, который Portainer не должен использовать
BOT_TOKEN = config("TELEGRAM_BOT_TOKEN", default=None)
# Путь, который будет смонтирован через Docker Volume.
# Он должен быть точно таким же, как в docker-compose volumes: /app/data
# DB_PATH импортируется из config.py
# ---------------------------------------

# Используем decouple для получения ADMIN_ID
admin_id_str = config("ADMIN_ID", default=None)

# !!! КРИТИЧНОЕ ИЗМЕНЕНИЕ: Проверка переменных после вызова config !!!
if not BOT_TOKEN:
    logging.error("Критическая ошибка: TELEGRAM_BOT_TOKEN не задан!")
    sys.exit(1)

if not admin_id_str:
    logging.error("Критическая ошибка: ADMIN_ID не задан!")
    sys.exit(1)

try:
    ADMIN_ID = int(admin_id_str)
except ValueError:
    logging.error(f"Ошибка: ADMIN_ID '{admin_id_str}' не является числом!")
    sys.exit(1)

# --- Глобальные переменные ---
structured_data = {}
FACULTIES_LIST = []
ALL_TEACHERS_LIST = [] 

# --- Вспомогательные функции для запуска скриптов ---

async def run_script(command: list, target: Optional[Message]) -> bool:
    """Запускает внешний скрипт и обрабатывает вывод."""
    
    # Мы используем sys.executable для обеспечения корректного запуска в Docker
    python_executable = sys.executable 
    
    process = await asyncio.create_subprocess_exec(
        python_executable, *command, 
        stdout=asyncio.subprocess.PIPE, 
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    script_name = command[0]
    
    if process.returncode != 0:
        error_output = stderr.decode('utf-8', errors='ignore').strip()
        error_message = f"❌ Ошибка `{script_name}`:\n```bash\n{error_output[-500:]}\n```"
        
        logging.error(f"Скрипт {script_name} завершился ошибкой. Подробности: {error_output}")
        
        if target:
            # Если вызвано из админ-панели
            await target.answer(error_message, parse_mode="Markdown"); 
        return False
    
    if target:
        # Для ручного запуска, показываем успешный вывод
        success_output = stdout.decode('utf-8', errors='ignore').strip()
        await target.answer(f"✅ Успешно выполнено: `{script_name}`\n```\n{success_output[-300:]}\n```", parse_mode="Markdown")
        
    return True

async def perform_full_update(bot: Bot, admin_id: int, target_message: Optional[Message] = None):
    """
    Выполняет полную цепочку обновления расписания (Fetch -> Process -> Reload).
    Может быть вызван планировщиком (target_message=None) или администратором.
    """
    if target_message:
        # Для администратора
        await target_message.answer("🚀 Начинаю полное обновление...", reply_markup=types.ReplyKeyboardRemove())
    
    logging.info("Starting full update sequence...")
    
    success = True
    
    # 1. Fetch (Скрапинг)
    if not await run_script(["fetch_schedule.py"], target_message):
        success = False
    
    # 2. Process (Парсинг)
    if success and not await run_script(["process_schedules.py"], target_message):
        success = False
        
    # 3. Reload structure (Перезагрузка данных в память бота)
    if success:
        data, faculties, teachers = await load_structure_from_db()
        if faculties:
            global structured_data, FACULTIES_LIST, ALL_TEACHERS_LIST
            structured_data = data
            FACULTIES_LIST = faculties
            ALL_TEACHERS_LIST = teachers
            if target_message:
                await target_message.answer("✅ Полное обновление успешно завершено!", reply_markup=admin_keyboard)
            else:
                await bot.send_message(admin_id, "✅ Запланированное обновление расписания (Fetch+Parse) успешно завершено и структура перезагружена.")
        else:
            success = False
            if target_message:
                await target_message.answer("❗️ Обновление завершено с ошибкой при перезагрузке структуры.", reply_markup=admin_keyboard)
            else:
                await bot.send_message(admin_id, "❌ Запланированное обновление завершено с ошибкой при перезагрузке структуры.")
    else:
        if target_message:
            await target_message.answer("❗️ Обновление прервано из-за ошибки (см. логи выше).", reply_markup=admin_keyboard)




# --- Первичная инициализация (будет выполнена в main()) ---
# Инициализация БД и загрузка структуры перенесены в async функцию main()

# --- FSM, Фильтры, Клавиатуры (без изменений) ---
class CourseCallbackFactory(CallbackData, prefix="course"):
    course_id: int
    faculty_id: int
class TeacherSearch(StatesGroup): name, matches = State(), State()
class Broadcast(StatesGroup): waiting_for_message = State()
class SessionResults(StatesGroup): waiting_for_record_book_number = State()
class NoteEdit(StatesGroup): waiting_for_note_text = State()
class ChecklistAdd(StatesGroup): waiting_for_item_text = State()
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool: return message.from_user.id == ADMIN_ID

def get_faculties_keyboard():
    builder = InlineKeyboardBuilder()
    [builder.button(text=name, callback_data=f"faculty:{i}") for i, name in enumerate(FACULTIES_LIST)]; builder.adjust(2)
    return builder.as_markup()
def get_courses_keyboard(faculty_id: int): # <--- Ожидаем число (ID)
    # Используем ID для получения имени факультета (строки)
    faculty = FACULTIES_LIST[faculty_id] 
    
    builder = InlineKeyboardBuilder()
    courses = sorted(structured_data.get(faculty, {}).keys(), key=lambda c: int(c) if c.isdigit() else 99)
    
    if not courses:
         logging.warning(f"Не найдены курсы для факультета: {faculty}")
         # Если курсов нет, возвращаем только кнопку "Назад"
         builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
         return builder.as_markup()
         
    for course in courses:
        # Убедимся, что 'course' можно безопасно конвертировать в int для CourseCallbackFactory
        try:
            course_int = int(course)
        except ValueError:
             logging.error(f"Не удалось конвертировать курс '{course}' в число. Пропуск.")
             continue
             
        builder.button(
            text=f"{course} курс",
            # Передаем числа в фабрику
            callback_data=CourseCallbackFactory(course_id=course_int, faculty_id=faculty_id)
        )
        
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
    return builder.as_markup()
def get_groups_keyboard(faculty: str, course: str):
    builder = InlineKeyboardBuilder()
    groups = sorted(structured_data.get(faculty, {}).get(course, []))
    [builder.button(text=g, callback_data=f"group:{g}") for g in groups]; builder.adjust(2)
    
    # --- ИСПРАВЛЕНИЕ ЗДЕСЬ ---
    # FACULTIES_LIST.index(faculty) возвращает ID (число)
    faculty_id = FACULTIES_LIST.index(faculty) 
    
    builder.row(InlineKeyboardButton(
        text=f"⬅️ Назад к курсам ({faculty})", 
        # Передаем ID, а не строковое имя в колбэк-дату
        callback_data=f"back_to_courses:{faculty_id}" 
    ))
    return builder.as_markup()
def get_teacher_choices_keyboard(teachers: List[str]):
    builder = InlineKeyboardBuilder()
    [builder.button(text=name, callback_data=f"teacher_select:{i}") for i, name in enumerate(teachers)]; builder.adjust(1)
    return builder.as_markup()
def get_teacher_nav_keyboard(current_offset: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Пред. день", callback_data=f"teacher_nav:{current_offset - 1}")
    builder.button(text="След. день ➡️", callback_data=f"teacher_nav:{current_offset + 1}")
    return builder.as_markup()

def get_session_results_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Заметки", callback_data="notes_root")
    builder.button(text="🔄 Обновить", callback_data="refresh_results")
    builder.button(text="✏️ Изменить номер", callback_data="change_record_book")
    builder.button(text="⚙️ Настройки", callback_data="session_settings")
    builder.adjust(2)
    return builder.as_markup()

def get_settings_keyboard(settings: dict):
    builder = InlineKeyboardBuilder()
    
    # Toggles
    # hide_5: Hide > 4 (Excellent)
    # hide_4: Hide > 3 (Good)
    # hide_3: Hide > 2 (Satisfactory) - usually we want to hide passed exams
    # hide_passed: Hide all passed (Зачет, 3, 4, 5)
    # hide_failed: Hide failed (Незачет, Недопуск)
    
    s = settings
    
    def btn(key, label):
        status = "✅" if s.get(key, False) else "❌"
        return InlineKeyboardButton(text=f"{label} {status}", callback_data=f"toggle_setting:{key}")

    builder.row(btn("hide_5", "Скрыть 'Отлично' (5)"))
    builder.row(btn("hide_4", "Скрыть 'Хорошо' (4)"))
    builder.row(btn("hide_3", "Скрыть 'Удовл.' (3)"))
    builder.row(btn("hide_passed_non_exam", "Скрыть 'Зачет'"))
    builder.row(btn("hide_failed", "Скрыть 'Незачет/Недопуск'"))
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад к результатам", callback_data="back_to_results"))
    return builder.as_markup()

day_selection_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")], [KeyboardButton(text="Пн"), KeyboardButton(text="Вт"), KeyboardButton(text="Ср")], [KeyboardButton(text="Чт"), KeyboardButton(text="Пт"), KeyboardButton(text="Сб")], [KeyboardButton(text="📊 Мои результаты"), KeyboardButton(text="/start")]], resize_keyboard=True)
admin_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔄 Обновить расписание"), KeyboardButton(text="📥 Перезагрузить структуру")], [KeyboardButton(text="⬅️ Выйти из админ-панели")]], resize_keyboard=True)



# --- Хэндлеры ---
dp = Dispatcher(storage=MemoryStorage())

@dp.callback_query(CourseCallbackFactory.filter())
async def process_course_choice_factory(callback: CallbackQuery, callback_data: CourseCallbackFactory):
    
    # Используем данные из фабрики:
    faculty_name = FACULTIES_LIST[callback_data.faculty_id]
    course_name = str(callback_data.course_id) # курс как строка
    
    await callback.message.edit_text(
        f"Факультет: *{faculty_name}*, Курс: *{course_name}*.\n\nВыберите вашу группу:", 
        reply_markup=get_groups_keyboard(faculty_name, course_name), 
        parse_mode="Markdown"
    )
    await callback.answer()

def format_schedule_message(group: str, target_date: date, lessons: List[sqlite3.Row]) -> str:
    months = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    date_str = f"{weekdays[target_date.weekday()]} {target_date.day} {months[target_date.month - 1]}"
    
    if not lessons:
        week_number = target_date.isocalendar()[1]
        week_type = "Четная" if week_number % 2 == 0 else "Нечетная"
        header = f"*{week_type} неделя*\n*{group}*\n\n*{date_str}*"
        return f"{header}\n❌Расписание отсутствует❌"
        
    week_type = lessons[0]['week_type'].capitalize()
    header = f"*{week_type} неделя*\n*{group}*\n\n*{date_str}*"
    # Note: access fields by key ['time']
    lesson_parts = [f"⏰ {lesson['time']}\n-  `{lesson['subject']}`\n-  `{lesson['teacher']}`\n-  `{lesson['location']}`" for lesson in lessons]
    return f"{header}\n\n" + "\n\n".join(lesson_parts)

async def show_teacher_schedule(target: Message | CallbackQuery, teacher_name: str, day_offset: int):
    target_date = date.today() + timedelta(days=day_offset)
    date_str = target_date.strftime('%Y-%m-%d')
    
    # ИСПОЛЬЗУЕМ АСИНХРОННЫЙ ВЫЗОВ
    lessons_raw = await get_schedule_by_teacher(teacher_name, date_str)
    
    # Группировка по парам и сбор групп
    merged_lessons = {}
    for lesson in lessons_raw:
        # Поскольку aiosqlite.Row ведет себя как dict, используем его ключи
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
            groups_str = ", ".join(sorted(list(set(groups)))) # Сортируем и убираем дубли
            part = f"⏰ {lesson['time']} {group_prefix} *{groups_str}*\n-  `{lesson['subject']}`\n-  `{lesson['location']}`"
            lesson_parts.append(part)
        text = f"{header}\n\n" + "\n\n".join(lesson_parts)
        
        
    keyboard = get_teacher_nav_keyboard(day_offset)
    
    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        # Используем edit_text только если контент действительно изменился, чтобы избежать лишних уведомлений
        if target.message.text != text: 
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await target.answer()

@dp.message(CommandStart())
async def send_welcome(message: Message):
    # Проверяем, есть ли у пользователя уже выбранная группа
    user_group = await get_user_group_db(message.from_user.id)
    
    if user_group:
        # Если группа есть, приветствуем и сразу предлагаем посмотреть расписание
        await message.answer(
            f"👋 С возвращением! Ваша группа: *{user_group}*.\n\n"
            "Вы можете посмотреть расписание на выбранный день.",
            reply_markup=day_selection_keyboard,
            parse_mode="Markdown"
        )
    else:
        # Если группы нет, запускаем стандартный процесс настройки
        await save_user_group_db(message.from_user.id, None)
        await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\n"
                             "Для поиска по группе - выберите ваш факультет.\n"
                             "Для поиска по преподавателю - просто напишите его фамилию.",
                             reply_markup=get_faculties_keyboard())

@dp.message(lambda message: message.text in ["Show schedule for a course", "Показать расписание для курса"])
async def get_course(message: types.Message):
    """
    Этот обработчик будет вызван, когда пользователь отправит "Show schedule for a course" или "Показать расписание для курса"
    """
    # Получаем список курсов из базы данных
    courses = await get_all_courses()
    if not courses:
        await message.reply("В базе данных не найдено ни одного курса.")
        return

    # Создаем клавиатуру с кнопками курсов
    keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
    for course in courses:
        keyboard.add(types.KeyboardButton(course))

    await message.reply("Пожалуйста, выберите курс:", reply_markup=keyboard)

# --- Хэндлеры Студентов (Выбор группы) ---
@dp.callback_query(F.data.startswith("faculty:"))
async def process_faculty_choice(callback: CallbackQuery):
    # Извлекаем ID: faculty:ID
    parts = callback.data.split(":")
    faculty_id = int(parts[1]) # <-- Получаем числовой ID
    
    # Используем ID для получения имени, чтобы показать пользователю
    faculty_name = FACULTIES_LIST[faculty_id] 
    
    # ТЕПЕРЬ ПЕРЕДАЕМ ЧИСЛОВОЙ ID в get_courses_keyboard
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", 
        reply_markup=get_courses_keyboard(faculty_id), # <-- Передаем ID (число)
        parse_mode="Markdown"
    )
    await callback.answer()
@dp.callback_query(F.data.startswith("course:"))
async def process_course_choice(callback: CallbackQuery):
    _, faculty, course = callback.data.split(":")
    await callback.message.edit_text(f"Факультет: *{faculty}*, Курс: *{course}*.\n\nВыберите вашу группу:", reply_markup=get_groups_keyboard(faculty, course), parse_mode="Markdown")
    await callback.answer()
@dp.callback_query(F.data.startswith("group:"))
async def process_group_choice(callback: CallbackQuery):
    group = callback.data.split(":")[1]
    # Используем async DB call
    await save_user_group_db(callback.from_user.id, group)
    await callback.message.delete()
    await callback.message.answer(f"Отлично! Ваша группа *{group}* сохранена.", reply_markup=day_selection_keyboard, parse_mode="Markdown")
    await callback.answer()
@dp.callback_query(F.data == "back_to_faculties")
async def back_to_faculties(callback: CallbackQuery):
    await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard())
    await callback.answer()
@dp.callback_query(F.data.startswith("back_to_courses:"))
async def back_to_courses(callback: CallbackQuery):
    # Извлекаем ID: back_to_courses:ID
    try:
        faculty_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        await callback.answer("Ошибка навигации: неверный формат ID факультета.", show_alert=True)
        await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard())
        return

    faculty_name = FACULTIES_LIST[faculty_id]
    
    # Передаем ЧИСЛОВОЙ ID в get_courses_keyboard
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", 
        reply_markup=get_courses_keyboard(faculty_id), 
        parse_mode="Markdown"
    )
    await callback.answer()

# --- Хэндлеры Студентов (Расписание по дням) ---
def get_date_by_day_name(day_name: str) -> date:
    today = date.today()
    if day_name == "Сегодня": return today
    if day_name == "Завтра": return today + timedelta(days=1)
    days_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5}
    target_weekday = days_map[day_name]
    days_ahead = target_weekday - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return today + timedelta(days_ahead)


@dp.message(F.text.in_({"Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"}))
async def send_schedule(message: Message):
    # Используем async DB call
    group = await get_user_group_db(message.from_user.id)
    if not group: await message.answer("Пожалуйста, сначала выберите группу /start"); return
    try:
        target_date = get_date_by_day_name(message.text)
        date_str = target_date.strftime('%Y-%m-%d')
        
        # ИСПОЛЬЗУЕМ АСИНХРОННЫЙ DAL
        lessons = await get_schedule_by_group(group, date_str)
        
        response_text = format_schedule_message(group, target_date, lessons)
        await message.answer(response_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка при отправке расписания: {e}"); await message.answer("Произошла внутренняя ошибка.")


# --- Хэндлеры Результатов Сессии ---
@dp.message(F.text == "📊 Мои результаты")
async def show_session_results(message: Message, state: FSMContext):
    # 1. Проверяем, есть ли номер зачетки в БД
    record_book_number = await get_record_book_number(message.from_user.id)
    
    if not record_book_number:
        await message.answer(
            "Для просмотра результатов мне нужно знать номер вашей зачетной книжки.\n"
            "Пожалуйста, введите его (только цифры):"
        )
        await state.set_state(SessionResults.waiting_for_record_book_number)
        return

    await show_results_view(message, message.from_user.id, record_book_number)

async def show_results_view(target: Message | CallbackQuery, user_id: int, record_book_number: str):
    # Helper to show results (used by command and back button)
    
    if isinstance(target, Message):
        msg = await target.answer(f"🔍 Ищу результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")
    else:
        # For callback, we might want to edit, but scraping takes time.
        # Better to answer callback and send new message or edit with "Loading..."
        await target.message.edit_text(f"🔍 Ищу результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")
        msg = target.message

    settings = await get_user_settings(user_id)
    results_data = await UsurtScraper.get_session_results(record_book_number)
    
    if results_data is None:
        text = "❌ Не удалось получить результаты. Попробуйте позже или проверьте номер зачетки."
        if isinstance(target, Message):
            await msg.edit_text(text, reply_markup=get_session_results_keyboard())
        else:
            await msg.edit_text(text, reply_markup=get_session_results_keyboard())
        return

    # Filter and Format
    formatted_text = format_results(results_data, settings)
    
    # Split if too long
    if len(formatted_text) > 4000:
        parts = [formatted_text[i:i+4000] for i in range(0, len(formatted_text), 4000)]
        for i, part in enumerate(parts):
            markup = get_session_results_keyboard() if i == len(parts) - 1 else None
            if i == 0:
                await msg.edit_text(part, parse_mode="Markdown", reply_markup=markup)
            else:
                await msg.answer(part, parse_mode="Markdown", reply_markup=markup)
    else:
        await msg.edit_text(formatted_text, parse_mode="Markdown", reply_markup=get_session_results_keyboard())

def filter_results_by_settings(data: list, settings: dict) -> list:
    """
    Фильтрует результаты сессии согласно настройкам пользователя.
    Возвращает отфильтрованный список.
    """
    filtered = []
    for item in data:
        # Filtering Logic
        if settings.get("hide_5") and item.get('grade_value') == 5: continue
        if settings.get("hide_4") and item.get('grade_value') == 4: continue
        if settings.get("hide_3") and item.get('grade_value') == 3: continue
        
        # Hide "Зачет" (passed but no grade value)
        if settings.get("hide_passed_non_exam") and item.get('passed') and item.get('grade_value') is None: continue
        
        # Hide Failed
        if settings.get("hide_failed") and not item.get('passed'): continue
        
        filtered.append(item)
    
    return filtered

def format_results(data: list, settings: dict) -> str:
    if not data:
        return "📭 Результаты не найдены."

    # Apply filters
    filtered_data = filter_results_by_settings(data, settings)
    
    if not filtered_data:
        return "📭 Все предметы скрыты настройками фильтрации."

    # Group by semester
    semesters = {}
    for item in filtered_data:
        sem = item['semester']
        if sem not in semesters: semesters[sem] = []
        semesters[sem].append(item)
    
    output = []
    
    for sem, items in semesters.items():
        semester_lines = []
        for item in items:
            # Format Line
            icon = "✅" if item['passed'] else "⚠️"
            if not item['passed']: icon = "❌"
            
            line = f"{icon} *{item['subject']}*\n   🎓 {item['grade']}"
            if item['date']:
                line += f" ({item['date']})"
            
            semester_lines.append(line)
        
        if semester_lines:
            output.append(f"\n📅 *{sem}*")
            output.extend(semester_lines)
        
    return "\n".join(output)

@dp.callback_query(F.data == "session_settings")
async def open_settings(callback: CallbackQuery):
    settings = await get_user_settings(callback.from_user.id)
    await callback.message.edit_text(
        "⚙️ *Настройки отображения*\n\n"
        "Выберите, какие предметы нужно **СКРЫТЬ**:",
        reply_markup=get_settings_keyboard(settings),
        parse_mode="Markdown"
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("toggle_setting:"))
async def toggle_setting(callback: CallbackQuery):
    key = callback.data.split(":")[1]
    user_id = callback.from_user.id
    
    settings = await get_user_settings(user_id)
    settings[key] = not settings.get(key, False) # Toggle
    
    await update_user_settings(user_id, settings)
    
    # Update keyboard
    await callback.message.edit_reply_markup(reply_markup=get_settings_keyboard(settings))
    await callback.answer("Настройка обновлена")

@dp.callback_query(F.data == "back_to_results")
async def back_to_results(callback: CallbackQuery):
    record_book_number = await get_record_book_number(callback.from_user.id)
    if record_book_number:
        await show_results_view(callback, callback.from_user.id, record_book_number)
    else:
        await callback.message.edit_text("Ошибка: номер зачетки не найден.")

@dp.message(SessionResults.waiting_for_record_book_number)
async def process_record_book_number(message: Message, state: FSMContext):
    number = message.text.strip()
    
    if not number.isdigit():
        await message.answer("⚠️ Номер зачетной книжки должен состоять только из цифр. Попробуйте еще раз.")
        return
        
    # Сохраняем в БД
    await save_record_book_number(message.from_user.id, number)
    await state.clear()
    
    # Сразу вызываем поиск
    await show_results_view(message, message.from_user.id, number)

@dp.callback_query(F.data == "refresh_results")
async def refresh_session_results(callback: CallbackQuery):
    record_book_number = await get_record_book_number(callback.from_user.id)
    if not record_book_number:
        await callback.answer("Номер зачетки не найден.")
        return
        
    await callback.message.edit_text(f"🔄 Обновляю результаты для зачетки: *{record_book_number}*...", parse_mode="Markdown")
    
    # Force scrape (use_cache=False)
    # Note: We don't unpack here anymore!
    data = await UsurtScraper.get_session_results(record_book_number, use_cache=False)
    
    if data is None:
        await callback.message.edit_text("❌ Не удалось обновить результаты.", reply_markup=get_session_results_keyboard())
    else:
        # Show updated results
        await show_results_view(callback, callback.from_user.id, record_book_number)
    
    await callback.answer()


# --- Хэндлеры Заметок ---

@dp.callback_query(F.data == "notes_root")
async def notes_root(callback: CallbackQuery):
    # Показываем список семестров из кэша
    record_book_number = await get_record_book_number(callback.from_user.id)
    if not record_book_number:
        await callback.answer("Сначала получите результаты сессии.")
        return

    # Получаем данные из кэша (без скрапинга)
    data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
    
    if not data:
        await callback.answer("Нет данных о предметах. Обновите результаты.")
        return
    
    # Применяем фильтры пользователя
    settings = await get_user_settings(callback.from_user.id)
    filtered_data = filter_results_by_settings(data, settings)
    
    if not filtered_data:
        await callback.answer("Все предметы скрыты фильтрами. Измените настройки.")
        return

    # Собираем уникальные семестры из отфильтрованных данных
    semesters = sorted(list(set(d['semester'] for d in filtered_data)))
    
    builder = InlineKeyboardBuilder()
    for sem in semesters:
        builder.button(text=sem, callback_data=f"notes_sem:{sem}")
    
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к результатам", callback_data="back_to_results"))
    
    await callback.message.edit_text("📂 Выберите семестр для заметок:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("notes_sem:"))
async def notes_semester_select(callback: CallbackQuery):
    semester = callback.data.split(":", 1)[1]
    record_book_number = await get_record_book_number(callback.from_user.id)
    data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
    
    # Применяем фильтры пользователя
    settings = await get_user_settings(callback.from_user.id)
    filtered_data = filter_results_by_settings(data, settings)
    
    # Фильтруем предметы этого семестра (исключаем пустые)
    subjects = sorted(list(set(d['subject'] for d in filtered_data if d['semester'] == semester and d['subject'].strip())))
    
    builder = InlineKeyboardBuilder()
    for subj in subjects:
        # Ограничиваем длину callback_data (64 байта)
        # Используем хэш или просто обрезаем, но для простоты пока передаем индекс в списке
        # Но список может меняться... Лучше передать короткое имя или ID если бы был.
        # Попробуем передать имя, надеясь что оно влезет. Если нет - надо делать mapping.
        # Для надежности сделаем mapping через временный кэш или просто передадим индекс в отсортированном списке
        builder.button(text=subj[:30], callback_data=f"notes_subj:{semester}:{subjects.index(subj)}")
        
    builder.adjust(1)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к семестрам", callback_data="notes_root"))
    
    await callback.message.edit_text(f"📂 Семестр: {semester}\nВыберите предмет:", reply_markup=builder.as_markup())
    await callback.answer()

@dp.callback_query(F.data.startswith("notes_subj:"))
async def notes_subject_view(callback: CallbackQuery, state: FSMContext):
    try:
        _, semester, subj_idx_str = callback.data.split(":")
        subj_idx = int(subj_idx_str)
        
        record_book_number = await get_record_book_number(callback.from_user.id)
        data = await UsurtScraper.get_session_results(record_book_number, use_cache=True)
        subjects = sorted(list(set(d['subject'] for d in data if d['semester'] == semester)))
        subject_name = subjects[subj_idx]
        
        # Сохраняем контекст
        await state.update_data(current_subject=subject_name, current_semester=semester)
        
        await show_subject_note_view(callback, callback.from_user.id, subject_name, semester)
    except Exception as e:
        logging.error(f"Error in notes_subject_view: {e}")
        await callback.answer("Ошибка при открытии заметки.", show_alert=True)

async def show_subject_note_view(target: Message | CallbackQuery, user_id: int, subject_name: str, semester: str):
    from database import get_subject_note
    note_data = await get_subject_note(user_id, subject_name)
    
    note_text = note_data.get("note_text", "")
    checklist = note_data.get("checklist", [])
    
    text = f"📝 *{subject_name}*\n\n"
    if note_text:
        text += f"{note_text}\n\n"
    else:
        text += "_Нет заметки_\n\n"
        
    if checklist:
        text += "*Чек-лист:*\n"
        for i, item in enumerate(checklist):
            status = "✅" if item['done'] else "⬜"
            text += f"{status} {item['text']}\n"
    else:
        text += "_Чек-лист пуст_"
        
    # Клавиатура
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Ред. заметку", callback_data="note_edit_text")
    builder.button(text="➕ Пункт чек-листа", callback_data="note_add_item")
    
    # Кнопки для чек-листа
    for i, item in enumerate(checklist):
        status_icon = "✅" if item['done'] else "⬜"
        builder.button(text=f"{status_icon} {item['text'][:15]}...", callback_data=f"note_toggle:{i}")
        builder.button(text="🗑", callback_data=f"note_del:{i}")
    
    builder.adjust(2) # Ред, Добавить
    # Далее по 2 кнопки на строку (Тоггл, Удалить)
    
    builder.row(InlineKeyboardButton(text=f"⬅️ Назад к предметам", callback_data=f"notes_sem:{semester}"))
    
    if isinstance(target, Message):
        await target.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")
    else:
        await target.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data == "note_edit_text")
async def note_edit_text_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст заметки:")
    await state.set_state(NoteEdit.waiting_for_note_text)
    await callback.answer()

@dp.message(NoteEdit.waiting_for_note_text)
async def note_edit_text_save(message: Message, state: FSMContext):
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    from database import get_subject_note, save_subject_note
    current_data = await get_subject_note(message.from_user.id, subject_name)
    
    await save_subject_note(message.from_user.id, subject_name, message.text, current_data.get("checklist", []))
    
    await state.set_state(None) # Clear state but keep data
    # Restore state data for navigation
    await state.update_data(current_subject=subject_name, current_semester=semester)
    
    # Show updated view (need to find the last message or send new)
    # Sending new is easier
    await show_subject_note_view(message, message.from_user.id, subject_name, semester)

@dp.callback_query(F.data == "note_add_item")
async def note_add_item_start(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer("Введите текст пункта чек-листа:")
    await state.set_state(ChecklistAdd.waiting_for_item_text)
    await callback.answer()

@dp.message(ChecklistAdd.waiting_for_item_text)
async def note_add_item_save(message: Message, state: FSMContext):
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    from database import get_subject_note, save_subject_note
    current_data = await get_subject_note(message.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    
    checklist.append({"text": message.text, "done": False})
    
    await save_subject_note(message.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
    
    await state.set_state(None)
    await state.update_data(current_subject=subject_name, current_semester=semester)
    await show_subject_note_view(message, message.from_user.id, subject_name, semester)

@dp.callback_query(F.data.startswith("note_toggle:"))
async def note_toggle_item(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    from database import get_subject_note, save_subject_note
    current_data = await get_subject_note(callback.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    
    if 0 <= idx < len(checklist):
        checklist[idx]['done'] = not checklist[idx]['done']
        await save_subject_note(callback.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
        
    await show_subject_note_view(callback, callback.from_user.id, subject_name, semester)
    await callback.answer()

@dp.callback_query(F.data.startswith("note_del:"))
async def note_delete_item(callback: CallbackQuery, state: FSMContext):
    idx = int(callback.data.split(":")[1])
    data = await state.get_data()
    subject_name = data.get("current_subject")
    semester = data.get("current_semester")
    
    from database import get_subject_note, save_subject_note
    current_data = await get_subject_note(callback.from_user.id, subject_name)
    checklist = current_data.get("checklist", [])
    
    if 0 <= idx < len(checklist):
        checklist.pop(idx)
        await save_subject_note(callback.from_user.id, subject_name, current_data.get("note_text", ""), checklist)
        
    await show_subject_note_view(callback, callback.from_user.id, subject_name, semester)
    await callback.answer()


# --- Хэндлеры Преподавателей (без изменений, т.к. используют async show_teacher_schedule) ---
KNOWN_BUTTONS = {"Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "🔄 Обновить расписание", "📥 Перезагрузить структуру", "⬅️ Выйти из админ-панели", "Написать всем", "Удалить последнее"}
@dp.message(F.text, ~F.text.in_(KNOWN_BUTTONS), ~F.text.startswith('/'))
async def find_teacher_by_name(message: Message, state: FSMContext):
    await state.clear()
    search_query = message.text.strip().lower()
    
    matches = [name for name in ALL_TEACHERS_LIST if search_query in name.lower()]
    
    if not matches:
        await message.answer("😕 Преподаватель не найден. Попробуйте еще раз."); return
    if len(matches) == 1:
        await state.update_data(name=matches[0])
        await show_teacher_schedule(message, matches[0], 0); return
        
    await state.update_data(matches=matches)
    await message.answer("Найдено несколько преподавателей. Пожалуйста, выберите:", reply_markup=get_teacher_choices_keyboard(matches))

@dp.callback_query(F.data.startswith("teacher_nav:"))
async def process_teacher_nav(callback: CallbackQuery, state: FSMContext):
    day_offset = int(callback.data.split(":")[1])
    data = await state.get_data()
    teacher_name = data.get("name")
    
    if not teacher_name:
        await callback.answer("Ошибка: не удалось найти имя преподавателя. Пожалуйста, попробуйте снова.", show_alert=True)
        return
        
    await show_teacher_schedule(callback, teacher_name, day_offset)

# --- Хэндлеры Администратора ---
@dp.message(F.text == "/admin", IsAdmin())
async def admin_panel(message: Message):
    await message.answer("Добро пожаловать в админ-панель!", reply_markup=admin_keyboard)

@dp.message(F.text == "⬅️ Выйти из админ-панели", IsAdmin())
async def exit_admin_panel(message: Message):
    await message.answer("Вы вышли из админ-панели.", reply_markup=day_selection_keyboard)

@dp.message(F.text == "🔄 Обновить расписание", IsAdmin())
async def update_schedule(message: Message, bot: Bot):
    # Вызываем общую функцию обновления, передавая message для обратной связи
    await perform_full_update(bot, ADMIN_ID, target_message=message)

@dp.message(F.text == "📥 Перезагрузить структуру", IsAdmin())
async def reload_from_db(message: Message):
    global structured_data, FACULTIES_LIST, ALL_TEACHERS_LIST
    
    data, faculties, teachers = await load_structure_from_db()
    
    if faculties:
        structured_data = data
        FACULTIES_LIST = faculties
        ALL_TEACHERS_LIST = teachers
        await message.answer("✅ Структура меню и преподавателей успешно обновлена из БД!", reply_markup=admin_keyboard)
    else:
        await message.answer("❌ Не удалось перезагрузить структуру. Проверьте логи.", reply_markup=admin_keyboard)

@dp.message(Broadcast.waiting_for_message)
async def broadcast_message(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    # Используем async DB call
    user_ids = await get_all_user_ids()
    sent_message_ids = []
    success_count, fail_count = 0, 0

    await message.answer(f"Начинаю рассылку для {len(user_ids)} пользователей...", reply_markup=admin_keyboard)
    for user_id in user_ids:
        try:
            sent_msg = await bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
            sent_message_ids.append((user_id, sent_msg.message_id))
            success_count += 1
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение пользователю {user_id}: {e}")
            fail_count += 1
        await asyncio.sleep(0.1) 

    if sent_message_ids:
        await log_broadcast(sent_message_ids) # Используем async DB call

    await message.answer(
        f"✅ Рассылка завершена!\n\n"
        f"Успешно: {success_count}\n"
        f"Неуспешно: {fail_count}",
        reply_markup=admin_keyboard
    )

@dp.message(F.text == "Удалить последнее", IsAdmin())
async def delete_last_broadcast(message: Message, bot: Bot):
    last_broadcast = await get_last_broadcast() # Используем async DB call
    if not last_broadcast:
        await message.answer("Не найдено рассылок для удаления.", reply_markup=admin_keyboard)
        return

    # ... (дальнейшая логика удаления остается прежней)
    success_count, fail_count = 0, 0
    await message.answer(f"Начинаю удаление {len(last_broadcast)} сообщений...", reply_markup=admin_keyboard)

    for chat_id, message_id in last_broadcast:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
            success_count += 1
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение {message_id} в чате {chat_id}: {e}")
            fail_count += 1
        await asyncio.sleep(0.1)

    log_deleted_msg = "Запись о рассылке удалена из лога." if await delete_last_broadcast_log() else "Не удалось удалить запись о рассылке."

    await message.answer(
        f"✅ Удаление завершено!\n\n"
        f"Успешно удалено: {success_count}\n"
        f"Не удалось удалить: {fail_count}\n\n{log_deleted_msg}",
        reply_markup=admin_keyboard
    )

# --- Запуск бота и Планировщика ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    
    # 1. Инициализация базы данных
    await initialize_database()
    
    # 2. Первичная загрузка структуры из БД
    global structured_data, FACULTIES_LIST, ALL_TEACHERS_LIST
    data, faculties, teachers = await load_structure_from_db()
    if faculties:
        structured_data = data
        FACULTIES_LIST = faculties
        ALL_TEACHERS_LIST = teachers
        logging.info("Структура меню и преподавателей успешно загружены из БД при запуске.")
    else:
        logging.warning("Структура меню не загружена. База данных может быть пуста.")
    
    # 3. Инициализация планировщика
    scheduler = AsyncIOScheduler()
    
    # Планируем обновление на 11:00 и 20:00 ежедневно
    # Job ID нужен для возможности отслеживания и управления, если потребуется
    scheduler.add_job(
        perform_full_update, 
        'cron', 
        hour='11,20', 
        args=[bot, ADMIN_ID],
        id='daily_schedule_update',
        name='Ежедневное обновление расписания'
    )
    
    logging.info("Планировщик запущен: обновление в 11:00 и 20:00 (по времени, установленному в контейнере/системе).")
    scheduler.start()
    
    # 4. Запуск бота
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.warning("Бот остановлен вручную.")