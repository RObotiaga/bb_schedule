from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.callback_data import CallbackData
from typing import Any, Callable
import logging

# --- Callback Data Factory ---

class CourseCallbackFactory(CallbackData, prefix="crs"):
    mode: str       # "user" | "admin"
    course_id: int
    faculty_id: int

# --- Callback Prefix Mapping ---

CALLBACK_PREFIXES = {
    "user": {
        "faculty": "faculty",
        "back_faculty": "back_to_faculties",
        "back_courses": "back_to_courses",
        "group": "group",
    },
    "admin": {
        "faculty": "adm_fac",
        "back_faculty": "adm_back_fac",
        "back_courses": "adm_back_crs",
        "group": "adm_grp_name",
    },
}

# --- Static Keyboards ---

def get_welcome_inline_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="✏️ Изменить группу", callback_data="change_group")
    return builder.as_markup()

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
        [KeyboardButton(text="📊 Статус бота")],
        [KeyboardButton(text="🔄 Обновить расписание")],
        [KeyboardButton(text="📥 Перезагрузить структуру")],
        [KeyboardButton(text="🏆 Обновить рейтинг")],
        [KeyboardButton(text="📤 Экспорт рейтинга"), KeyboardButton(text="📥 Импорт рейтинга")],
        [KeyboardButton(text="👥 Группы"), KeyboardButton(text="📉 Статистика отчислений")],
        [KeyboardButton(text="📢 Рассылка"), KeyboardButton(text="📥 Загрузить БД")],
        [KeyboardButton(text="⬅️ Выйти из админ-панели")]
    ],
    resize_keyboard=True
)

broadcast_cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Отмена рассылки")]
    ],
    resize_keyboard=True
)

# --- Pagination Helper ---

def build_paginated_keyboard(
    items: list[Any],
    item_callback: Callable[[int, Any], tuple[str, str]],
    page: int = 0,
    per_page: int = 10,
    nav_callback_prefix: str = "page",
    back_text: str = "⬅️ Назад",
    back_callback: str = "back",
    columns: int = 1,
) -> InlineKeyboardMarkup:
    """Generic paginated keyboard builder.
    
    Args:
        items: full list of items to paginate
        item_callback: function(actual_index, item) -> (display_text, callback_data)
        page: current page (0-indexed)
        per_page: items per page
        nav_callback_prefix: prefix for pagination nav buttons
        back_text: text for back button
        back_callback: callback_data for back button
        columns: number of columns for items
    """
    builder = InlineKeyboardBuilder()
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_items = items[start_idx:end_idx]
    
    for i, item in enumerate(current_items):
        actual_idx = start_idx + i
        text, cb_data = item_callback(actual_idx, item)
        builder.button(text=text, callback_data=cb_data)
    
    builder.adjust(columns)
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{nav_callback_prefix}:{page - 1}"))
    if end_idx < len(items):
        nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"{nav_callback_prefix}:{page + 1}"))
    if nav_buttons:
        builder.row(*nav_buttons)
    
    builder.row(InlineKeyboardButton(text=back_text, callback_data=back_callback))
    return builder.as_markup()


# --- Unified Dynamic Keyboards ---

def get_faculties_keyboard(faculties_list: list, mode: str = "user"):
    prefix = CALLBACK_PREFIXES[mode]
    builder = InlineKeyboardBuilder()
    if not faculties_list:
        logging.warning("faculties_list is empty in get_faculties_keyboard")
        return builder.as_markup()
    for i, name in enumerate(faculties_list):
        builder.button(text=name, callback_data=f"{prefix['faculty']}:{i}")
    builder.adjust(2)
    return builder.as_markup()


def get_courses_keyboard(faculty_id: int, faculties_list: list, structured_data: dict, mode: str = "user"):
    if faculty_id < 0 or faculty_id >= len(faculties_list):
        logging.error(f"Invalid faculty_id: {faculty_id}")
        return None

    prefix = CALLBACK_PREFIXES[mode]
    faculty = faculties_list[faculty_id]
    builder = InlineKeyboardBuilder()
    raw_courses = structured_data.get(faculty, {}).keys()
    
    def sort_key(c):
        return int(c) if str(c).isdigit() else 99
    
    courses = sorted(raw_courses, key=sort_key)
    
    if not courses:
        logging.warning(f"Не найдены курсы для факультета: {faculty}")
        builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data=prefix["back_faculty"]))
        return builder.as_markup()
    
    for course in courses:
        try:
            course_int = int(course)
        except ValueError:
            logging.error(f"Не удалось конвертировать курс '{course}' в число. Пропуск.")
            continue
        builder.button(
            text=f"{course} курс",
            callback_data=CourseCallbackFactory(mode=mode, course_id=course_int, faculty_id=faculty_id)
        )
    
    builder.adjust(2)
    builder.row(InlineKeyboardButton(text="⬅️ Назад к факультетам", callback_data=prefix["back_faculty"]))
    return builder.as_markup()


def get_groups_keyboard(faculty: str, course: str, faculties_list: list, structured_data: dict, mode: str = "user"):
    prefix = CALLBACK_PREFIXES[mode]
    builder = InlineKeyboardBuilder()
    groups = sorted(structured_data.get(faculty, {}).get(course, []))
    for g in groups:
        builder.button(text=g, callback_data=f"{prefix['group']}:{g}")
    builder.adjust(2)
    
    try:
        faculty_id = faculties_list.index(faculty)
    except ValueError:
        faculty_id = 0
    
    builder.row(InlineKeyboardButton(
        text=f"⬅️ Назад к курсам ({faculty})",
        callback_data=f"{prefix['back_courses']}:{faculty_id}"
    ))
    return builder.as_markup()


# --- User-only Keyboards ---

def get_teacher_choices_keyboard(teachers: list):
    builder = InlineKeyboardBuilder()
    for i, name in enumerate(teachers):
        builder.button(text=name, callback_data=f"teacher_select:{i}")
    builder.adjust(1)
    return builder.as_markup()

def get_teacher_nav_keyboard(current_offset: int, is_subscribed: bool = False):
    builder = InlineKeyboardBuilder()
    
    if is_subscribed:
        builder.button(text="🔕 Отписаться", callback_data="teacher_sub:unsubscribe")
    else:
        builder.button(text="🔔 Подписаться", callback_data="teacher_sub:subscribe")
    
    builder.adjust(2)
    
    row_buttons = [
        InlineKeyboardButton(text="⬅️ Пред. день", callback_data=f"teacher_nav:{current_offset - 1}"),
        InlineKeyboardButton(text="След. день ➡️", callback_data=f"teacher_nav:{current_offset + 1}")
    ]
    builder.row(*row_buttons)
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
    
    buttons = [
        btn("hide_5", "Скрыть 'Отлично' (5)"),
        btn("hide_4", "Скрыть 'Хорошо' (4)"),
        btn("hide_3", "Скрыть 'Удовл.' (3)"),
        btn("hide_2", "Скрыть 'Неудовл.' (2)"),
        btn("hide_passed_non_exam", "Скрыть 'Зачет'"),
        btn("hide_failed", "Скрыть 'Незачет/Недопуск'")
    ]
    
    for b in buttons:
        builder.row(b)
    
    builder.row(InlineKeyboardButton(text="⬅️ Назад к результатам", callback_data="back_to_results"))
    return builder.as_markup()

def get_subjects_keyboard(subjects: list, page: int = 0, per_page: int = 10):
    def item_cb(idx, subj):
        display_text = subj[:40] + "..." if len(subj) > 40 else subj
        return display_text, f"subj_select:{idx}"
    
    kb = build_paginated_keyboard(
        items=subjects,
        item_callback=item_cb,
        page=page,
        per_page=per_page,
        nav_callback_prefix="subj_page",
        back_text="🔍 Поиск предмета",
        back_callback="subj_search_start",
        columns=1,
    )
    return kb


# --- Admin-only Keyboards ---

def get_admin_group_actions_keyboard(cluster_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="📚 Поиск по предметам", callback_data=f"adm_g_act_subj:{cluster_id}")
    builder.button(text="🔢 Поиск по номеру зачетки", callback_data=f"adm_g_act_rec:{cluster_id}")
    builder.button(text="⬅️ К списку факультетов", callback_data="adm_back_fac")
    builder.adjust(1)
    return builder.as_markup()

def get_admin_group_subjects_keyboard(cluster_id: int, subjects: list[str], page: int = 0, per_page: int = 10):
    def item_cb(idx, subj):
        display_text = subj[:40] + "..." if len(subj) > 40 else subj
        return display_text, f"adm_g_subj:{cluster_id}:{idx}"
    
    return build_paginated_keyboard(
        items=subjects,
        item_callback=item_cb,
        page=page,
        per_page=per_page,
        nav_callback_prefix=f"adm_g_subj_page:{cluster_id}",
        back_text="⬅️ Назад к действиям",
        back_callback=f"adm_grp:{cluster_id}",
        columns=1,
    )

def get_admin_group_record_books_keyboard(cluster_id: int, record_books: list[dict], page: int = 0, per_page: int = 15):
    from app.bot.fio_mapping import get_short_fio_by_record_book
    
    def item_cb(idx, rb_data):
        rb_num = rb_data['record_book']
        pass_rate = rb_data['pass_rate']
        display_name = get_short_fio_by_record_book(rb_num)
        return f"{display_name} ({pass_rate:.1f}%)", f"adm_g_rec:{cluster_id}:{idx}"
    
    return build_paginated_keyboard(
        items=record_books,
        item_callback=item_cb,
        page=page,
        per_page=per_page,
        nav_callback_prefix=f"adm_g_rec_page:{cluster_id}",
        back_text="⬅️ Назад к действиям",
        back_callback=f"adm_grp:{cluster_id}",
        columns=2,
    )
