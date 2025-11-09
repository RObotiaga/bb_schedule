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
    save_user_group_db, get_user_group_db, get_all_user_ids,
    log_broadcast, get_last_broadcast, delete_last_broadcast_log,
    get_schedule_by_group, get_schedule_by_teacher
)

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


# --- Функции для работы с базой данных (синхронные, используются только в show_teacher_schedule) ---
def get_db_connection():
    """Синхронное подключение к БД. Используется только в show_teacher_schedule для совместимости."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- Первичная инициализация (будет выполнена в main()) ---
# Инициализация БД и загрузка структуры перенесены в async функцию main()

# --- FSM, Фильтры, Клавиатуры (без изменений) ---
class CourseCallbackFactory(CallbackData, prefix="course"):
    course_id: int
    faculty_id: int
class TeacherSearch(StatesGroup): name, matches = State(), State()
class Broadcast(StatesGroup): waiting_for_message = State()
class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool: return message.from_user.id == ADMIN_ID

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
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM schedule WHERE teacher = ? AND lesson_date = ? ORDER BY time", (teacher_name, date_str))
    lessons_raw = cursor.fetchall()
    conn.close()
    
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
    await save_user_group_db(message.from_user.id, None)
    await message.answer("👋 Добро пожаловать! Я помогу вам узнать расписание.\n\n"
                         "Для поиска по группе - выберите ваш факультет.\n"
                         "Для поиска по преподавателю - просто напишите его фамилию.",
                         reply_markup=get_faculties_keyboard())

# --- Хэндлеры Студентов (Выбор группы) ---
@dp.callback_query(F.data.startswith("faculty:"))
async def process_faculty_choice(callback: CallbackQuery):
    faculty_name = FACULTIES_LIST[int(callback.data.split(":")[1])]
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
    faculty_name = FACULTIES_LIST[int(callback.data.split(":")[1])]
    await callback.message.edit_text(f"Вы выбрали: *{faculty_name}*.\n\nТеперь выберите курс:", reply_markup=get_courses_keyboard(faculty_name), parse_mode="Markdown")
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