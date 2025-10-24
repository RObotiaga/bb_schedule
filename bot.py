import asyncio
import logging
import json
import os
import re
import sys
import sqlite3
from datetime import date, timedelta
from typing import List

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DB_PATH = "data/schedule.db"

admin_id_str = os.getenv("ADMIN_ID")
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
ALL_TEACHERS_LIST = [] # <-- ИЗМЕНЕНИЕ: Новый список для всех преподавателей

# --- Фабрика для колбеков курсов ---
class CourseCallbackFactory(CallbackData, prefix="course"):
    course_id: int
    faculty_id: int

# --- Функции для работы с базой данных ---
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_structure_from_db():
    """Загружает структуру меню И список преподавателей из БД."""
    global structured_data, FACULTIES_LIST, ALL_TEACHERS_LIST
    if not os.path.exists(DB_PATH):
        logging.error(f"База данных '{DB_PATH}' не найдена! Запустите process_schedules.py.")
        return False
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Загрузка структуры
    cursor.execute("SELECT DISTINCT faculty, course, group_name FROM schedule ORDER BY faculty, course, group_name")
    rows = cursor.fetchall()
    temp_structured_data = {}
    for row in rows:
        faculty, course, group_name = row['faculty'], row['course'], row['group_name']
        if faculty not in temp_structured_data: temp_structured_data[faculty] = {}
        if course not in temp_structured_data[faculty]: temp_structured_data[faculty][course] = []
        if group_name not in temp_structured_data[faculty][course]: temp_structured_data[faculty][course].append(group_name)
    structured_data = temp_structured_data
    FACULTIES_LIST = sorted(structured_data.keys())
    
    # ИЗМЕНЕНИЕ: Загрузка списка преподавателей
    cursor.execute("SELECT DISTINCT teacher FROM schedule WHERE teacher != 'Не указан'")
    ALL_TEACHERS_LIST = sorted([row['teacher'] for row in cursor.fetchall()])
    
    conn.close()
    logging.info(f"Структура меню и {len(ALL_TEACHERS_LIST)} преподавателей успешно загружены из БД.")
    return True

def init_user_db():
    conn = get_db_connection()
    conn.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, group_name TEXT)")
    conn.commit(); conn.close()

def save_user_group_db(user_id: int, group_name: str | None):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO users (user_id, group_name) VALUES (?, ?)", (user_id, group_name))
    conn.commit(); conn.close()

def get_user_group_db(user_id: int) -> str | None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT group_name FROM users WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return row['group_name'] if row else None

# --- Первичная инициализация ---
init_user_db()
load_structure_from_db()

# --- FSM, Фильтры, Клавиатуры ---
class TeacherSearch(StatesGroup): name, matches = State(), State()
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool: return message.from_user.id == ADMIN_ID

# ... (все функции get_*_keyboard не изменились) ...
def get_faculties_keyboard():
    builder = InlineKeyboardBuilder()
    [builder.button(text=name, callback_data=f"faculty:{i}") for i, name in enumerate(FACULTIES_LIST)]; builder.adjust(2)
    return builder.as_markup()
def get_courses_keyboard(faculty_id: int):
    faculty = FACULTIES_LIST[faculty_id]
    builder = InlineKeyboardBuilder()
    courses = sorted(structured_data.get(faculty, {}).keys(), key=lambda c: int(c) if c.isdigit() else 99)
    for course in courses:
        builder.button(
            text=f"{course} курс",
            callback_data=CourseCallbackFactory(course_id=int(course), faculty_id=faculty_id)
        )
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
    return builder.as_markup()
def get_groups_keyboard(faculty: str, course: str):
    builder = InlineKeyboardBuilder()
    groups = sorted(structured_data.get(faculty, {}).get(course, []))
    [builder.button(text=g, callback_data=f"group:{g}") for g in groups]; builder.adjust(2)
    builder.row(InlineKeyboardButton(text=f"⬅️ Назад к курсам ({faculty})", callback_data=f"back_to_courses:{FACULTIES_LIST.index(faculty)}"))
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
day_selection_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")], [KeyboardButton(text="Пн"), KeyboardButton(text="Вт"), KeyboardButton(text="Ср")], [KeyboardButton(text="Чт"), KeyboardButton(text="Пт"), KeyboardButton(text="Сб")], [KeyboardButton(text="/start")]], resize_keyboard=True)
admin_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔄 Обновить расписание"), KeyboardButton(text="📥 Перезагрузить структуру")], [KeyboardButton(text="⬅️ Выйти из админ-панели")]], resize_keyboard=True)

# --- Хэндлеры ---
dp = Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def send_welcome(message: Message):
    save_user_group_db(message.from_user.id, None)
    await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\n"
                         "Для поиска по группе - выберите ваш факультет.\n"
                         "Для поиска по преподавателю - просто напишите его фамилию.",
                         reply_markup=get_faculties_keyboard())

# ... (форматирование и show_teacher_schedule не изменились) ...
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
    lesson_parts = [f"⏰ {lesson['time']}\n-  `{lesson['subject']}`\n-  `{lesson['teacher']}`\n-  `{lesson['location']}`" for lesson in lessons]
    return f"{header}\n\n" + "\n\n".join(lesson_parts)

async def show_teacher_schedule(target: Message | CallbackQuery, teacher_name: str, day_offset: int):
    target_date = date.today() + timedelta(days=day_offset)
    date_str = target_date.strftime('%Y-%m-%d')
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedule WHERE teacher = ? AND lesson_date = ? ORDER BY time", (teacher_name, date_str))
    lessons_raw = cursor.fetchall()
    conn.close()
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
            groups_str = ", ".join(groups)
            part = f"⏰ {lesson['time']} {group_prefix} *{groups_str}*\n-  `{lesson['subject']}`\n-  `{lesson['location']}`"
            lesson_parts.append(part)
        text = f"{header}\n\n" + "\n\n".join(lesson_parts)
    keyboard = get_teacher_nav_keyboard(day_offset)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        if target.message.text != text: await target.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await target.answer()

# --- Хэндлеры Студентов (без изменений) ---
@dp.callback_query(F.data.startswith("faculty:"))
async def process_faculty_choice(callback: CallbackQuery):
    faculty_id = int(callback.data.split(":")[1])
    faculty_name = FACULTIES_LIST[faculty_id]
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:",
        reply_markup=get_courses_keyboard(faculty_id),
        parse_mode="Markdown"
    )
    await callback.answer()
@dp.callback_query(CourseCallbackFactory.filter())
async def process_course_choice(callback: CallbackQuery, callback_data: CourseCallbackFactory):
    course_id = callback_data.course_id
    faculty_id = callback_data.faculty_id
    faculty = FACULTIES_LIST[faculty_id]
    await callback.message.edit_text(
        f"Факультет: *{faculty}*, Курс: *{course_id}*.\n\nВыберите вашу группу:",
        reply_markup=get_groups_keyboard(faculty, str(course_id)),
        parse_mode="Markdown"
    )
    await callback.answer()
@dp.callback_query(F.data.startswith("group:"))
async def process_group_choice(callback: CallbackQuery):
    group = callback.data.split(":")[1]
    save_user_group_db(callback.from_user.id, group)
    await callback.message.delete()
    await callback.message.answer(f"Отлично! Ваша группа *{group}* сохранена.", reply_markup=day_selection_keyboard, parse_mode="Markdown")
    await callback.answer()
@dp.callback_query(F.data == "back_to_faculties")
async def back_to_faculties(callback: CallbackQuery):
    await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard())
    await callback.answer()
@dp.callback_query(F.data.startswith("back_to_courses:"))
async def back_to_courses(callback: CallbackQuery):
    faculty_id = int(callback.data.split(":")[1])
    faculty_name = FACULTIES_LIST[faculty_id]
    await callback.message.edit_text(
        f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:",
        reply_markup=get_courses_keyboard(faculty_id),
        parse_mode="Markdown"
    )
    await callback.answer()
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
    group = get_user_group_db(message.from_user.id)
    if not group: await message.answer("Пожалуйста, сначала выберите группу /start"); return
    try:
        target_date = get_date_by_day_name(message.text)
        date_str = target_date.strftime('%Y-%m-%d')
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM schedule WHERE group_name = ? AND lesson_date = ? ORDER BY time", (group, date_str))
        lessons = cursor.fetchall()
        conn.close()
        response_text = format_schedule_message(group, target_date, lessons)
        await message.answer(response_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка при отправке расписания: {e}"); await message.answer("Произошла внутренняя ошибка.")

# --- Хэндлеры Преподавателей ---
@dp.callback_query(F.data.startswith("teacher_select:"))
async def process_teacher_selection(callback: CallbackQuery, state: FSMContext):
    selection_index = int(callback.data.split(":")[1])
    data = await state.get_data(); teacher_matches = data.get('matches', [])
    if selection_index >= len(teacher_matches):
        await callback.message.edit_text("Ошибка выбора. Попробуйте снова."); return
    selected_teacher = teacher_matches[selection_index]
    await state.update_data(name=selected_teacher)
    await show_teacher_schedule(callback, selected_teacher, 0)

@dp.callback_query(F.data.startswith("teacher_nav:"))
async def navigate_teacher_schedule(callback: CallbackQuery, state: FSMContext):
    day_offset = int(callback.data.split(":")[1])
    data = await state.get_data(); teacher_name = data.get('name')
    if not teacher_name:
        await callback.message.edit_text("Ваш выбор преподавателя истек. Начните поиск заново."); return
    await show_teacher_schedule(callback, teacher_name, day_offset)

KNOWN_BUTTONS = {"Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "🔄 Обновить расписание", "📥 Перезагрузить структуру", "⬅️ Выйти из админ-панели"}
@dp.message(F.text, ~F.text.in_(KNOWN_BUTTONS), ~F.text.startswith('/'))
async def find_teacher_by_name(message: Message, state: FSMContext):
    """Ищет преподавателя по фамилии в списке, загруженном в память."""
    await state.clear()
    search_query = message.text.strip().lower()
    
    # ИЗМЕНЕНИЕ: Поиск происходит в Python-списке, а не в БД
    matches = [name for name in ALL_TEACHERS_LIST if search_query in name.lower()]
    
    if not matches:
        await message.answer("😕 Преподаватель не найден. Попробуйте еще раз."); return
    if len(matches) == 1:
        await state.update_data(name=matches[0])
        await show_teacher_schedule(message, matches[0], 0); return
        
    await state.update_data(matches=matches)
    await message.answer("Найдено несколько преподавателей. Пожалуйста, выберите:", reply_markup=get_teacher_choices_keyboard(matches))

# --- Хэндлеры Администратора (без изменений) ---
@dp.message(F.text == "/admin", IsAdmin())
async def admin_panel(message: Message):
    await message.answer("Добро пожаловать в админ-панель!", reply_markup=admin_keyboard)

@dp.message(F.text == "⬅️ Выйти из админ-панели", IsAdmin())
async def exit_admin_panel(message: Message):
    await message.answer("Вы вышли из админ-панели.", reply_markup=day_selection_keyboard)

async def run_script(command: list, message: Message):
    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        error_message = f"❌ Ошибка `{command[-1]}`:\n`{stderr.decode('utf-8', errors='ignore')}`"
        await message.answer(error_message[:4096], parse_mode="Markdown"); return False
    return True

@dp.message(F.text == "🔄 Обновить расписание", IsAdmin())
async def update_schedule(message: Message):
    await message.answer("🚀 Начинаю полное обновление...", reply_markup=types.ReplyKeyboardRemove())
    python_executable = sys.executable
    if await run_script([python_executable, "fetch_schedule.py"], message) and \
       await run_script([python_executable, "process_schedules.py"], message) and \
       load_structure_from_db(): # <-- ВАЖНО: Перезагружаем структуру после обновления
        await message.answer("✅ Полное обновление успешно завершено!", reply_markup=admin_keyboard)
    else:
        await message.answer("❗️Обновление прервано из-за ошибки.", reply_markup=admin_keyboard)

@dp.message(F.text == "📥 Перезагрузить структуру", IsAdmin())
async def reload_from_db(message: Message):
    if load_structure_from_db():
        await message.answer("✅ Структура меню и преподавателей успешно обновлена из БД!", reply_markup=admin_keyboard)
    else:
        await message.answer("❌ Не удалось перезагрузить структуру.", reply_markup=admin_keyboard)

# --- Запуск бота ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(main())