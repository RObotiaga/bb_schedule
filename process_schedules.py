# FILE: process_schedules.py
import pandas as pd
import os
import sqlite3
import re
import logging
from datetime import datetime
import sys
from decouple import config

# --- КОНФИГУРАЦИЯ (УНИФИКАЦИЯ ПУТЕЙ) ---
from config import DB_PATH, DOWNLOAD_DIR
# Используем централизованную настройку логгирования
from utils import setup_logging

SCHEDULES_DIR = DOWNLOAD_DIR
# -------------------------------------------------------------
setup_logging()
CURRENT_YEAR = datetime.now().year

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ (без изменений) ---
MONTHS_MAP = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

def determine_week_type(filename):
    filename_lower = filename.lower()
    if 'нечетная' in filename_lower or 'нечет' in filename_lower: return 'нечетная'
    if 'четная' in filename_lower or 'чет' in filename_lower: return 'четная'
    if 'аттестация' in filename_lower or 'сессия' in filename_lower: return 'сессия'
    return 'неизвестно'

def parse_filename_context(filename):
    """
    Extracts (semester, start_year, end_year) from filename if present.
    Example: "Промежуточная аттестация за 1 семестр 2025-2026 уч.год_..."
    Returns: (semester: int, start_year: int, end_year: int) or None
    """
    match = re.search(r'(\d)\s*семестр.*?(\d{4})[/-](\d{4})', filename, re.IGNORECASE)
    if match:
        return int(match.group(1)), int(match.group(2)), int(match.group(3))
    return None

def parse_date_from_cell(cell_content, context):
    """
    Parses date from cell content.
    context: dict with optional keys 'semester', 'start_year', 'end_year'
             OR simple 'year' (int) for backward compatibility
    """
    if not isinstance(cell_content, str): return None
    
    # 1. Попытка парсинга формата "16.01.2026" или "16.01.26"
    # Сначала ищем этот формат, т.к. он более строгий.
    date_match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{2,4})', cell_content)
    if date_match:
        try:
            day = int(date_match.group(1))
            month = int(date_match.group(2))
            year_str = date_match.group(3)
            year = int(year_str)
            if len(year_str) == 2:
                year += 2000 # Предполагаем 21 век
            
            return datetime(year, month, day).strftime('%Y-%m-%d')
        except ValueError:
            pass # Если дата некорректная (напр. 32.01), пробуем дальше
            
    # 2. Попытка парсинга текстового формата "16 января"
    match = re.search(r'(\d+)\s+([а-я]+)', cell_content, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_str = match.group(2).lower()
        month = MONTHS_MAP.get(month_str)
        if month:
            target_year = context.get('year', datetime.now().year)
            
            # Если есть контекст семестра и учебного года - используем его
            if 'semester' in context:
                semester = context['semester']
                start_year = context['start_year']
                end_year = context['end_year']
                
                if semester == 1:
                    # 1 семестр: сентябрь-декабрь -> начало года, январь-февраль -> конец года
                    if month >= 9:
                        target_year = start_year
                    else:
                        target_year = end_year
                elif semester == 2:
                    # 2 семестр: всегда вторая часть учебного года (весна)
                    target_year = end_year
            
            else:
                 # Старая логика (heuristic)
                current_date = datetime.now()
                # Если месяц > текущего (прошлое) и сейчас начало года (янв/фев), 
                # то это скорее всего прошлый год (декабрь). 
                # НО: Это работает плохо для расписаний на будущее.
                # Лучше полагаться на то, что расписание обычно актуальное.
                if month > 9 and current_date.month < 5:
                    target_year = current_date.year - 1
                else:
                    target_year = current_date.year

            try:
                return datetime(target_year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                return None
    return None

def parse_lesson_cell(cell_content):
    # ... (логика парсинга пары)
    if not isinstance(cell_content, str) or cell_content.strip() == "": return None
    # FIX: Robustly remove leading hyphens and whitespace
    lines = [re.sub(r'^\s*-\s*', '', line).strip() for line in cell_content.split('\n') if line.strip()]
    if not lines: return None
        
    subject = lines[0]
    teacher = "Не указан"
    location_parts = []
    
    # Поиск преподавателя и аудитории
    # Начинаем поиск со второй строки (индекс 1)
    if len(lines) > 1:
        teacher_found = False
        for i in range(1, len(lines)):
            line = lines[i]
            
            # Пропускаем "Не указан" если мы еще не нашли преподавателя
            if not teacher_found and line.lower() == "не указан":
                continue
            
            # Если преподаватель еще не найден, проверяем текущую строку
            if not teacher_found:
                # Heuristics for teacher detection:
                # 1. Contains academic rank keywords
                is_academic = any(keyword in line.lower() for keyword in ["преподаватель", "доцент", "профессор", "ассистент", "зав. кафедрой"])
                
                # 2. Looks like a name: "Surname I.O." or "Surname First Middle"
                # Matches: "Ivanov I.I.", "Ivanov I. I.", "Ivanov Ivan Ivanovich"
                is_name_format = re.match(r'^[А-ЯЁ][а-яё\-]+\s+([А-ЯЁ]\.\s*[А-ЯЁ]\.|[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)', line)
                
                # 3. Old regex (reversed?): I.O. Surname (kept just in case, though rare in RU schedules usually)
                is_name_initials_first = re.match(r'^[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+', line)

                if is_academic or is_name_format or is_name_initials_first or (line.istitle() and len(line.split()) >= 2):
                    teacher = line
                    teacher_found = True
                else:
                    # Если строка не похожа на преподавателя, это часть локации/другое
                    location_parts.append(line)
            else:
                # Если преподаватель уже найден, все остальное - локация
                location_parts.append(line)
                
        if location_parts:
            location = " ".join(location_parts)
        else:
            location = "Не указана"
    else:
        location = "Не указана"
            
    # Добавление информации о подгруппе
    subgroup_info = ""
    # Ищем подгруппу в исходном контенте
    subgroup_match = re.search(r'(\d\s*п/г)', cell_content, re.IGNORECASE)
    if subgroup_match:
        subgroup_info = f" ({subgroup_match.group(1).replace(' ', '')})"
        
    return {"subject": subject + subgroup_info, "teacher": teacher, "location": location}

def create_db_tables(conn):
    """Создает структуру таблиц в базе данных."""
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_name TEXT NOT NULL,
        lesson_date TEXT NOT NULL,
        time TEXT NOT NULL,
        subject TEXT NOT NULL,
        teacher TEXT NOT NULL,
        location TEXT NOT NULL,
        week_type TEXT,
        faculty TEXT,
        course TEXT
    )
    """)
    cursor.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, group_name TEXT)")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_date ON schedule (group_name, lesson_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_teacher_date ON schedule (teacher, lesson_date)")
    conn.commit()

def process_single_file(file_path, faculty="Неизвестно", course="N/A"):
    """
    Обрабатывает один файл расписания и возвращает список уроков.
    """
    lessons_list = []
    filename = os.path.basename(file_path)
    
    if not filename.endswith((".xls", ".xlsx")):
        return []
    
    week_type = determine_week_type(filename)
    if week_type == 'неизвестно':
        logging.warning(f"      ПРЕДУПРЕЖДЕНИЕ: Не удалось определить тип недели для файла {filename}. Пропуск.")
        return []
    
    # Попытка извлечь контекст семестра/года из имени файла
    file_context = {}
    acad_context = parse_filename_context(filename)
    if acad_context:
        file_context['semester'] = acad_context[0]
        file_context['start_year'] = acad_context[1]
        file_context['end_year'] = acad_context[2]
    else:
        file_context['year'] = CURRENT_YEAR 
    
    try:
        # Чтение с автоматическим определением первой строки заголовка
        df = pd.read_excel(file_path, header=None)
    except Exception as e:
        logging.error(f"      Ошибка чтения файла {filename}: {e}. Пропуск.")
        return []

    # Поиск строки с заголовком 'День'
    header_row_index = -1
    
    for i, row in df.iterrows():
        row_str = [str(cell) for cell in row.tolist()]
        
        # Ищем стандартные заголовки
        if 'День' in row_str:
            header_row_index = i
            break
        # Или ищем альтернативные
        if 'Day' in row_str and 'Time' in row_str:
            header_row_index = i
            break

    if header_row_index == -1:
        logging.warning(f"      Не найден заголовок таблицы ('День') в {filename}. Пропуск.")
        return []

    # Повторное чтение с правильным заголовком
    df = pd.read_excel(file_path, header=header_row_index)
    # Приведение имен столбцов к строковому формату
    df.columns = [str(col).strip() for col in df.columns]

    # Определяем фактические названия столбцов для Дня и Часов
    day_col_name = 'День' if 'День' in df.columns else ('Day' if 'Day' in df.columns else None)
    time_col_name = 'Часы' if 'Часы' in df.columns else ('Time' if 'Time' in df.columns else None)

    if not day_col_name or not time_col_name:
         logging.warning(f"      Не найдены ключевые столбцы ('День'/'Часы') в {filename}. Пропуск.")
         return []

    # Определяем столбцы, которые являются группами
    groups = [col for col in df.columns if col not in [day_col_name, time_col_name, 'nan', 'Unnamed: 0']]
    current_date_str = None
    current_time_slot = None
    
    for index, row in df.iterrows():
        # FIX: передаем file_context вместо простого year
        potential_date = parse_date_from_cell(str(row.get(day_col_name)), file_context)
        if potential_date:
            current_date_str = potential_date
            # Сброс времени при нахождении новой даты, чтобы не переносить время с предыдущего дня
            current_time_slot = None
            
        if not current_date_str: continue
            
        raw_time = str(row.get(time_col_name, '')).strip()
        # Проверяем, есть ли валидное время
        if raw_time and "nan" not in raw_time.lower():
             time_slot = raw_time
             current_time_slot = time_slot
        elif current_time_slot:
             # Если время не указано, но есть сохраненное (объединенные ячейки)
             time_slot = current_time_slot
        else:
             # Нет ни текущего, ни сохраненного времени
             continue
            
        for group in groups:
            lesson_info = parse_lesson_cell(row.get(group))
            if lesson_info:
                # Структура: group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course
                lessons_list.append((
                    str(group).strip(), current_date_str, time_slot,
                    lesson_info['subject'], lesson_info['teacher'], lesson_info['location'],
                    week_type, faculty, course
                ))
    return lessons_list

# --- ОСНОВНАЯ ЛОГИКА ---
def main():
    if not os.path.exists(SCHEDULES_DIR):
        print(f"Ошибка: Директория для поиска расписаний '{SCHEDULES_DIR}' не найдена. Возможно, скрапинг не был запущен или завершился ошибкой.")
        sys.exit(1)
        
    conn = sqlite3.connect(DB_PATH)
    create_db_tables(conn)

    # 3. Парсинг файлов (БЕЗ удаления данных из БД пока)
    all_lessons_to_insert = []

    logging.info("Начинаем обработку файлов расписания...")
    for dirpath, _, filenames in os.walk(SCHEDULES_DIR):
        if dirpath == SCHEDULES_DIR:
            continue
            
        relative_path = os.path.relpath(dirpath, DOWNLOAD_DIR)
        path_parts = relative_path.split(os.sep)
        faculty = path_parts[0] if len(path_parts) > 0 else "Неизвестно"
        course_str = path_parts[1] if len(path_parts) > 1 else "Без курса"
        course_match = re.search(r'\d+', course_str)
        course = course_match.group(0) if course_match else "N/A"
        
        logging.info(f"\n--- Обработка папки: {faculty} / {course_str} ---")

        for filename in filenames:
            file_path = os.path.join(dirpath, filename)
            logging.info(f"    - Файл: {os.path.basename(file_path)}")
            
            lessons = process_single_file(file_path, faculty, course)
            all_lessons_to_insert.extend(lessons)
            
            if lessons:
                 logging.info(f"      Найдено занятий: {len(lessons)}")

    # 4. Проверка результатов и обновление БД
    if not all_lessons_to_insert:
        logging.warning("\n⚠️ Не было найдено ни одной пары для добавления. База данных НЕ обновлена (старые данные сохранены).")
        conn.close()
        return

    logging.info(f"\nНайдено {len(all_lessons_to_insert)} пар. Обновляем базу данных...")
    
    try:
        cursor = conn.cursor()
        # Используем транзакцию для атомарного обновления
        cursor.execute("BEGIN TRANSACTION;")
        
        logging.info("Очистка старых данных из таблицы 'schedule'...")
        cursor.execute("DELETE FROM schedule;")
        
        logging.info(f"Запись новых данных...")
        cursor.executemany("""
        INSERT INTO schedule (group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, all_lessons_to_insert)
        
        conn.commit()
        logging.info(f"\n✅ Готово! База данных '{os.path.basename(DB_PATH)}' успешно обновлена.")
        
    except Exception as e:
        conn.rollback()
        logging.error(f"\n❌ Ошибка при обновлении БД: {e}. Изменения отменены.")
        sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()