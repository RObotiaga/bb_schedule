from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
import logging

# Callback Data Factory
class CourseCallbackFactory(CallbackData, prefix="course"):
    course_id: int
    faculty_id: int

# Static Keyboards
day_selection_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Сегодня"), KeyboardButton(text="Завтра")],
        [KeyboardButton(text="Пн"), KeyboardButton(text="Вт"), KeyboardButton(text="Ср")],
        [KeyboardButton(text="Чт"), KeyboardButton(text="Пт"), KeyboardButton(text="Сб")],
        [KeyboardButton(text="📊 Мои результаты"), KeyboardButton(text="/start")]
    ],
    resize_keyboard=True
)

admin_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="🔄 Обновить расписание")],
        [KeyboardButton(text="📥 Перезагрузить структуру")],
        [KeyboardButton(text="⬅️ Выйти из админ-панели")]
    ],
    resize_keyboard=True
)

# Dynamic Keyboards

def get_faculties_keyboard(faculties_list: list):
    builder = InlineKeyboardBuilder()
    [builder.button(text=name, callback_data=f"faculty:{i}") for i, name in enumerate(faculties_list)]
    builder.adjust(2)
    return builder.as_markup()

def get_courses_keyboard(faculty_id: int, faculties_list: list, structured_data: dict):
    # Используем ID для получения имени факультета (строки)
    if faculty_id < 0 or faculty_id >= len(faculties_list):
        logging.error(f"Invalid faculty_id: {faculty_id}")
        return None

    faculty = faculties_list[faculty_id] 
    
    builder = InlineKeyboardBuilder()
    courses = sorted(structured_data.get(faculty, {}).keys(), key=lambda c: int(c) if c.isdigit() else 99)
    
    if not courses:
         logging.warning(f"Не найдены курсы для факультета: {faculty}")
         builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
         return builder.as_markup()
         
    for course in courses:
        try:
            course_int = int(course)
        except ValueError:
             logging.error(f"Не удалось конвертировать курс '{course}' в число. Пропуск.")
             continue
             
        builder.button(
            text=f"{course} курс",
            callback_data=CourseCallbackFactory(course_id=course_int, faculty_id=faculty_id)
        )
        
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data="back_to_faculties"))
    return builder.as_markup()

def get_groups_keyboard(faculty: str, course: str, faculties_list: list, structured_data: dict):
    builder = InlineKeyboardBuilder()
    groups = sorted(structured_data.get(faculty, {}).get(course, []))
    [builder.button(text=g, callback_data=f"group:{g}") for g in groups]
    builder.adjust(2)
    
    try:
        faculty_id = faculties_list.index(faculty)
    except ValueError:
        faculty_id = 0 # Fallback
    
    builder.row(InlineKeyboardButton(
        text=f"⬅️ Назад к курсам ({faculty})", 
        callback_data=f"back_to_courses:{faculty_id}" 
    ))
    return builder.as_markup()

def get_teacher_choices_keyboard(teachers: list):
    builder = InlineKeyboardBuilder()
    [builder.button(text=name, callback_data=f"teacher_select:{i}") for i, name in enumerate(teachers)]
    builder.adjust(1)
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
