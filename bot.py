import asyncio
import logging
import json
import os
import re
import sys
from datetime import date, timedelta
from typing import List

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart, BaseFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", 'placeholder:TELEGRAM_TOKEN_REMOVED')
SCHEDULE_JSON_PATH = "schedule.json"
ADMIN_ID = 936853523
USER_DATA_JSON_PATH = "user_data.json"

# --- Глобальные переменные ---
full_schedule = {}
teacher_schedule = {} # <-- Для расписания преподавателей
structured_data = {}
FACULTIES_LIST = []
user_choices = {}

# --- Функции для работы с данными ---
def load_and_prepare_data():
    global full_schedule, teacher_schedule, structured_data, FACULTIES_LIST
    if not os.path.exists(SCHEDULE_JSON_PATH):
        logging.warning(f"Файл '{SCHEDULE_JSON_PATH}' не найден.")
        return False
    try:
        with open(SCHEDULE_JSON_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # --- ИЗМЕНЕНИЕ: Загружаем обе структуры ---
            full_schedule = data.get("student_schedule", {})
            teacher_schedule = data.get("teacher_schedule", {})
    except json.JSONDecodeError:
        logging.error(f"Ошибка декодирования JSON в {SCHEDULE_JSON_PATH}."); return False
    
    # ... (остальной код функции без изменений) ...
    temp_structured_data = {}
    for group_name, group_sch in full_schedule.items():
        if not group_sch: continue
        try:
            first_date = next(iter(group_sch))
            first_lesson = group_sch[first_date][0]
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

# ... (функции load_user_data, save_user_data, get_user_group не изменились) ...
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
    else: user_choices = {}
    logging.info(f"Загружено {len(user_choices)} записей пользователей.")

def save_user_data(user_id: int, group: str | None):
    if group is None: user_choices.pop(user_id, None)
    else: user_choices[user_id] = group
    with open(USER_DATA_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(user_choices, f, ensure_ascii=False, indent=4)

def get_user_group(user_id: int) -> str | None:
    return user_choices.get(user_id)


load_and_prepare_data()
load_user_data()

# --- FSM для поиска преподавателя ---
class TeacherSearch(StatesGroup):
    name = State()
    matches = State()

# --- Фильтр администратора ---
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return message.from_user.id == ADMIN_ID

# --- Клавиатуры ---
# ... (get_faculties_keyboard, get_courses_keyboard, get_groups_keyboard не изменились) ...
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


# --- НОВЫЕ КЛАВИАТУРЫ ДЛЯ ПРЕПОДАВАТЕЛЕЙ ---
def get_teacher_choices_keyboard(teachers: List[str]):
    builder = InlineKeyboardBuilder()
    for index, name in enumerate(teachers):
        # Используем индекс, чтобы избежать ошибки с длинными callback_data
        builder.button(text=name, callback_data=f"teacher_select:{index}")
    builder.adjust(1)
    return builder.as_markup()

def get_teacher_nav_keyboard(current_offset: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="⬅️ Пред. день", callback_data=f"teacher_nav:{current_offset - 1}")
    builder.button(text="След. день ➡️", callback_data=f"teacher_nav:{current_offset + 1}")
    return builder.as_markup()

# ... (day_selection_keyboard, admin_keyboard не изменились) ...
day_selection_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")], [KeyboardButton(text="Пн"), KeyboardButton(text="Вт"), KeyboardButton(text="Ср")], [KeyboardButton(text="Чт"), KeyboardButton(text="Пт"), KeyboardButton(text="Сб")]], resize_keyboard=True)
admin_keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🔄 Обновить расписание"), KeyboardButton(text="📥 Перезагрузить JSON")], [KeyboardButton(text="⬅️ Выйти из админ-панели")]], resize_keyboard=True)


# --- Хэндлеры ---
dp = Dispatcher(storage=MemoryStorage()) # FSM требует хранилище

# ... (хэндлеры /start, admin, callback-и выбора группы и т.д. не изменились) ...
# (оставлены для полноты)
@dp.message(CommandStart())
async def send_welcome(message: Message):
    save_user_data(message.from_user.id, None)
    await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\n"
                         "Для поиска по группе - выберите ваш факультет.\n"
                         "Для поиска по преподавателю - просто напишите его фамилию.",
                         reply_markup=get_faculties_keyboard())

# --- НОВЫЕ ХЭНДЛЕРЫ ДЛЯ ПОИСКА ПРЕПОДАВАТЕЛЯ ---
# --- ЗАМЕНИТЕ ЭТУ ФУНКЦИЮ в bot.py ---
async def show_teacher_schedule(target: Message | CallbackQuery, teacher_name: str, day_offset: int, state: FSMContext):
    """
    Универсальная функция для отображения расписания преподавателя.
    С корректным форматированием заголовка и грамотным отображением групп.
    """
    target_date = date.today() + timedelta(days=day_offset)
    date_str = target_date.strftime('%Y-%m-%d')
    lessons = teacher_schedule.get(teacher_name, {}).get(date_str, [])
    
    months = ["Января", "Февраля", "Марта", "Апреля", "Мая", "Июня", "Июля", "Августа", "Сентября", "Октября", "Ноября", "Декабря"]
    weekdays = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    date_formatted = f"{weekdays[target_date.weekday()]} {target_date.day} {months[target_date.month - 1]}"

    if not lessons:
        week_number = target_date.isocalendar()[1]
        week_type = "Четная" if week_number % 2 == 0 else "Нечетная"
        # ИЗМЕНЕНИЕ: Заголовок теперь всегда жирный
        header = f"*{week_type} неделя*\n*{teacher_name}*\n\n*{date_formatted}*"
        text = f"{header}\n❌Расписание отсутствует❌"
    else:
        week_type = lessons[0]['week_type'].capitalize()
        # ИЗМЕНЕНИЕ: Заголовок теперь всегда жирный
        header = f"*{week_type} неделя*\n*{teacher_name}*\n\n*{date_formatted}*"
        lesson_parts = []
        for lesson in lessons:
            # ИЗМЕНЕНИЕ: Логика для выбора "группой" или "группами"
            groups = lesson.get('group', [])
            group_prefix = "с группой" if len(groups) == 1 else "с группами"
            groups_str = ", ".join(groups)
            
            part = (
                f"⏰ {lesson['time']} {group_prefix} *{groups_str}*\n"
                f"-  `{lesson['subject']}`\n"
                f"-  `{lesson['location']}`"
            )
            lesson_parts.append(part)
        text = f"{header}\n\n" + "\n\n".join(lesson_parts)

    keyboard = get_teacher_nav_keyboard(day_offset)
    if isinstance(target, Message):
        await target.answer(text, reply_markup=keyboard, parse_mode="Markdown")
    elif isinstance(target, CallbackQuery):
        if target.message.text != text:
            await target.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        await target.answer()

@dp.callback_query(F.data.startswith("teacher_select:"))
async def process_teacher_selection(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор конкретного преподавателя из списка."""
    selection_index = int(callback.data.split(":")[1])
    data = await state.get_data()
    teacher_matches = data.get('matches', [])
    
    if selection_index >= len(teacher_matches):
        await callback.message.edit_text("Ошибка выбора. Попробуйте снова.")
        return

    selected_teacher = teacher_matches[selection_index]
    await state.update_data(name=selected_teacher) # Сохраняем выбранного преподавателя в FSM
    
    await show_teacher_schedule(callback, selected_teacher, 0, state)

@dp.callback_query(F.data.startswith("teacher_nav:"))
async def navigate_teacher_schedule(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает навигацию по дням для преподавателя."""
    day_offset = int(callback.data.split(":")[1])
    data = await state.get_data()
    teacher_name = data.get('name')
    if not teacher_name:
        await callback.message.edit_text("Ваш выбор преподавателя истек. Пожалуйста, начните поиск заново.")
        return
        
    await show_teacher_schedule(callback, teacher_name, day_offset, state)

@dp.message(F.text == "/admin", IsAdmin())
async def admin_panel(message: Message):
    """Открывает админ-панель для администратора."""
    await message.answer("Добро пожаловать в админ-панель!", reply_markup=admin_keyboard)

@dp.message(F.text == "⬅️ Выйти из админ-панели", IsAdmin())
async def exit_admin_panel(message: Message):
    """Выход из админ-панели."""
    await message.answer("Вы вышли из админ-панели.", reply_markup=day_selection_keyboard)

async def run_script(command: list, message: Message):
    """Асинхронно запускает внешний скрипт и сообщает о результате."""
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    
    if process.returncode != 0:
        error_message = (
            f"❌ Ошибка выполнения скрипта `{command[-1]}` (код: {process.returncode}):\n"
            f"```\n{stderr.decode('utf-8', errors='ignore')}\n```"
        )
        await message.answer(error_message[:4096], parse_mode="Markdown") # Ограничиваем длину сообщения
        return False
    return True

@dp.message(F.text == "🔄 Обновить расписание", IsAdmin())
async def update_schedule(message: Message):
    """Запускает полный цикл обновления расписания."""
    await message.answer("🚀 Начинаю полное обновление расписания...", reply_markup=types.ReplyKeyboardRemove())
    python_executable = sys.executable
    
    if await run_script([python_executable, "fetch_schedule.py"], message) and \
       await run_script([python_executable, "process_schedules.py"], message) and \
       load_and_prepare_data():
        await message.answer("✅ Полное обновление успешно завершено!", reply_markup=admin_keyboard)
    else:
        await message.answer("❗️Обновление прервано из-за ошибки.", reply_markup=admin_keyboard)

@dp.message(F.text == "📥 Перезагрузить JSON", IsAdmin())
async def reload_from_json(message: Message):
    """Перезагружает данные только из JSON файла."""
    await message.answer("Перезагружаю данные из `schedule.json`...", parse_mode="Markdown")
    if load_and_prepare_data():
        await message.answer("✅ Данные в боте успешно обновлены!", reply_markup=admin_keyboard)
    else:
        await message.answer("❌ Не удалось перезагрузить данные.", reply_markup=admin_keyboard)

# Этот хэндлер должен быть одним из последних, т.к. он "ловит" произвольный текст
KNOWN_BUTTONS = {"Сегодня", "Завтра", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб",
                 "🔄 Обновить расписание", "📥 Перезагрузить JSON", "⬅️ Выйти из админ-панели"}
@dp.message(F.text, ~F.text.in_(KNOWN_BUTTONS), ~F.text.startswith('/'))
async def find_teacher_by_name(message: Message, state: FSMContext):
    """Ищет преподавателя по фамилии."""
    await state.clear() # Очищаем предыдущий поиск
    search_query = message.text.strip().lower()
    
    # Ищем все совпадения по фамилии
    matches = [name for name in teacher_schedule.keys() if search_query in name.lower()]
    
    if not matches:
        await message.answer("😕 Преподаватель с такой фамилией не найден. Попробуйте еще раз.")
        return
        
    if len(matches) == 1:
        teacher_name = matches[0]
        await state.update_data(name=teacher_name)
        await show_teacher_schedule(message, teacher_name, 0, state)
        return

    # Если найдено несколько, предлагаем выбрать
    await state.update_data(matches=matches) # Сохраняем список найденных
    await message.answer(
        "Найдено несколько преподавателей. Пожалуйста, выберите нужного:",
        reply_markup=get_teacher_choices_keyboard(matches)
    )

# ... (старые хэндлеры для расписания студентов) ...
# (оставлены для полноты)
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
    """Форматирует сообщение с расписанием для студента с копируемыми элементами."""
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
    
    lesson_parts = []
    for lesson in lessons:
        # ИЗМЕНЕНИЕ: Оборачиваем нужные части в `...`
        part = (
            f"⏰ {lesson['time']}\n"
            f"-  `{lesson['subject']}`\n"
            f"-  `{lesson['teacher']}`\n"
            f"-  `{lesson['location']}`"
        )
        lesson_parts.append(part)
        
    return f"{header}\n\n" + "\n\n".join(lesson_parts)

# --- Запуск бота ---
async def main():
    bot = Bot(token=BOT_TOKEN)
    await dp.start_polling(bot)

if __name__ == '__main__':
    # ... (старый код, оставлен для полноты) ...
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    asyncio.run(main())