import asyncio
import logging
import json
import os
import re
import sys
from datetime import date, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, BaseFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", 'placeholder:TELEGRAM_TOKEN_REMOVED')
SCHEDULE_JSON_PATH = "schedule.json"
ADMIN_ID = 936853523

USER_DATA_JSON_PATH = "user_data.json"

# --- Глобальные переменные для хранения данных ---
full_schedule = {}
structured_data = {}
FACULTIES_LIST = []
user_choices = {}

# --- Функции для работы с файлами данных ---
def load_user_data():
    global user_choices
    if os.path.exists(USER_DATA_JSON_PATH):
        try:
            with open(USER_DATA_JSON_PATH, 'r', encoding='utf-8') as f:
                user_choices = json.load(f)
                user_choices = {int(k): v for k, v in user_choices.items()}
        except (json.JSONDecodeError, TypeError):
            logging.error("Ошибка чтения user_data.json. Будет создан новый.")
            user_choices = {}
    else:
        user_choices = {}
    logging.info(f"Загружено {len(user_choices)} записей пользователей.")

def save_user_data(user_id: int, group: str | None):
    if group is None:
        user_choices.pop(user_id, None) # Удаляем пользователя, если группа None
    else:
        user_choices[user_id] = group
    with open(USER_DATA_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(user_choices, f, ensure_ascii=False, indent=4)

def get_user_group(user_id: int) -> str | None:
    return user_choices.get(user_id)

def load_and_prepare_data():
    global full_schedule, structured_data, FACULTIES_LIST
    if not os.path.exists(SCHEDULE_JSON_PATH):
        logging.warning(f"Файл '{SCHEDULE_JSON_PATH}' не найден.")
        return False
    try:
        with open(SCHEDULE_JSON_PATH, 'r', encoding='utf-8') as f:
            full_schedule = json.load(f)
    except json.JSONDecodeError:
        logging.error(f"Ошибка декодирования JSON в {SCHEDULE_JSON_PATH}.")
        return False
    
    temp_structured_data = {}
    for group_name, group_schedule in full_schedule.items():
        if not group_schedule: continue
        try:
            first_date = next(iter(group_schedule))
            first_lesson = group_schedule[first_date][0]
            faculty, course = first_lesson['faculty'], first_lesson['course']
            if faculty not in temp_structured_data: temp_structured_data[faculty] = {}
            if course not in temp_structured_data[faculty]: temp_structured_data[faculty][course] = []
            if group_name not in temp_structured_data[faculty][course]: temp_structured_data[faculty][course].append(group_name)
        except (StopIteration, IndexError, KeyError) as e:
            logging.error(f"Не удалось обработать группу '{group_name}'. Ошибка: {e}")
    
    structured_data = temp_structured_data
    FACULTIES_LIST = sorted(structured_data.keys())
    logging.info("Данные расписания успешно загружены.")
    return True

load_and_prepare_data()
load_user_data()

# --- Фильтр администратора ---
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == ADMIN_ID

# --- Клавиатуры ---
def get_faculties_keyboard():
    builder = InlineKeyboardBuilder()
    for index, faculty_name in enumerate(FACULTIES_LIST):
        builder.button(text=faculty_name, callback_data=f"faculty:{index}")
    builder.adjust(2)
    return builder.as_markup()

def get_courses_keyboard(faculty: str):
    builder = InlineKeyboardBuilder()
    courses = sorted(structured_data.get(faculty, {}).keys(), key=lambda c: int(c) if c.isdigit() else 99)
    for course in courses:
        builder.button(text=f"{course} курс", callback_data=f"course:{faculty}:{course}")
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
    return builder.as_markup()

def get_groups_keyboard(faculty: str, course: str):
    builder = InlineKeyboardBuilder()
    groups = sorted(structured_data.get(faculty, {}).get(course, []))
    for group in groups:
        builder.button(text=group, callback_data=f"group:{group}")
    builder.adjust(2)
    faculty_index = FACULTIES_LIST.index(faculty)
    builder.row(InlineKeyboardButton(text=f"⬅️ Назад к курсам ({faculty})", callback_data=f"back_to_courses:{faculty_index}"))
    return builder.as_markup()

day_selection_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")], [KeyboardButton(text="Пн"), KeyboardButton(text="Вт"), KeyboardButton(text="Ср")], [KeyboardButton(text="Чт"), KeyboardButton(text="Пт"), KeyboardButton(text="Сб")], [KeyboardButton(text="/start")]], resize_keyboard=True)
admin_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔄 Обновить расписание"), KeyboardButton(text="📥 Перезагрузить JSON")], [KeyboardButton(text="⬅️ Выйти из админ-панели")]], resize_keyboard=True)

# --- Хэндлеры ---
dp = Dispatcher()

@dp.message(CommandStart())
async def send_welcome(message: Message):
    save_user_data(message.from_user.id, None)
    await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\nПожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard())
    
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
       load_and_prepare_data():
        await message.answer("✅ Полное обновление успешно завершено!", reply_markup=admin_keyboard)
    else:
        await message.answer("❗️Обновление прервано из-за ошибки.", reply_markup=admin_keyboard)

@dp.message(F.text == "📥 Перезагрузить JSON", IsAdmin())
async def reload_from_json(message: Message):
    await message.answer("Перезагружаю данные из `schedule.json`...", parse_mode="Markdown")
    if load_and_prepare_data():
        await message.answer("✅ Данные в боте успешно обновлены!", reply_markup=admin_keyboard)
    else:
        await message.answer("❌ Не удалось перезагрузить данные.", reply_markup=admin_keyboard)

@dp.callback_query(F.data.startswith("faculty:"))
async def process_faculty_choice(callback: CallbackQuery):
    faculty_index = int(callback.data.split(":")[1])
    faculty_name = FACULTIES_LIST[faculty_index]
    await callback.message.edit_text(f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", reply_markup=get_courses_keyboard(faculty_name), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("course:"))
async def process_course_choice(callback: CallbackQuery):
    _, faculty, course = callback.data.split(":")
    await callback.message.edit_text(f"Факультет: *{faculty}*, Курс: *{course}*.\n\nВыберите вашу группу:", reply_markup=get_groups_keyboard(faculty, course), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data.startswith("group:"))
async def process_group_choice(callback: CallbackQuery):
    group = callback.data.split(":")[1]
    save_user_data(callback.from_user.id, group)
    await callback.message.delete()
    await callback.message.answer(f"Отлично! Ваша группа *{group}* сохранена.\n\nИспользуйте кнопки ниже для просмотра расписания.", reply_markup=day_selection_keyboard, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "back_to_faculties")
async def back_to_faculties(callback: CallbackQuery):
    await callback.message.edit_text("Пожалуйста, выберите ваш факультет:", reply_markup=get_faculties_keyboard())
    await callback.answer()

@dp.callback_query(F.data.startswith("back_to_courses:"))
async def back_to_courses(callback: CallbackQuery):
    faculty_index = int(callback.data.split(":")[1])
    faculty_name = FACULTIES_LIST[faculty_index]
    await callback.message.edit_text(f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", reply_markup=get_courses_keyboard(faculty_name), parse_mode="Markdown")
    await callback.answer()

def get_date_by_day_name(day_name: str) -> date:
    today = date.today()
    if day_name == "Сегодня": return today
    if day_name == "Завтра": return today + timedelta(days=1)
    days_map = {"Пн": 0, "Вт": 1, "Ср": 2, "Чт": 3, "Пт": 4, "Сб": 5}
    target_weekday = days_map[day_name]
    days_ahead = target_weekday - today.weekday()
    if days_ahead < 0: days_ahead += 7
    return today + timedelta(days=days_ahead)

def format_schedule_message(group: str, target_date: date, lessons: list) -> str:
    if not lessons: return f"На *{target_date.strftime('%d %B')}* для группы *{group}* пар нет."
    week_type = lessons[0]['week_type'].capitalize()
    months = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    date_str = f"{weekdays[target_date.weekday()]} {target_date.day} {months[target_date.month - 1]}"
    header = f"*{week_type} неделя*\n*{group}*\n\n*{date_str}*"
    lesson_parts = [f"⏰ {lesson['time']}\n-  {lesson['subject']}\n-  {lesson['teacher']}\n-  {lesson['location']}" for lesson in lessons]
    return f"{header}\n\n" + "\n\n".join(lesson_parts)

@dp.message(F.text.in_({"Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"}))
async def send_schedule(message: Message):
    group = get_user_group(message.from_user.id)
    if not group:
        await message.answer("Пожалуйста, сначала выберите группу с помощью команды /start")
        return
    try:
        target_date = get_date_by_day_name(message.text)
        date_str = target_date.strftime('%Y-%m-%d')
        lessons = full_schedule.get(group, {}).get(date_str, [])
        response_text = format_schedule_message(group, target_date, lessons)
        await message.answer(response_text, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Ошибка при отправке расписания: {e}")
        await message.answer("Произошла внутренняя ошибка.")

async def main():
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)

# --- ИСПРАВЛЕННЫЙ БЛОК ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(main())