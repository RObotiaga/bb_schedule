# FILE: process_schedules.py
import pandas as pd
import os
import sqlite3
import re
import logging # <-- Добавлен импорт
from datetime import datetime
import sys
from decouple import config

# --- КОНФИГУРАЦИЯ (УНИФИКАЦИЯ ПУТЕЙ) ---
# Используем пути из config.py для согласованности
from config import DB_PATH, DOWNLOAD_DIR
SCHEDULES_DIR = DOWNLOAD_DIR
# -------------------------------------------------------------
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
    return 'неизвестно'

def parse_date_from_cell(cell_content, year):
    # ... (логика парсинга даты)
    if not isinstance(cell_content, str): return None
    match = re.search(r'(\d+)\s+([а-я]+)', cell_content, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_str = match.group(2).lower()
        month = MONTHS_MAP.get(month_str)
        if month:
            # Обработка возможного переноса года (например, декабрь прошлого года)
            current_date = datetime.now()
            target_year = year
            if month > current_date.month and current_date.month < 3: # Если сейчас начало года, а месяц декабрь/ноябрь
                 target_year = year - 1
            
            try:
                return datetime(target_year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                return None
    return None

def parse_lesson_cell(cell_content):
    # ... (логика парсинга пары)
    if not isinstance(cell_content, str) or cell_content.strip() == "": return None
    lines = [re.sub(r'^-', '', line).strip() for line in cell_content.split('\n') if line.strip()]
    if not lines: return None
        
    subject = lines[0]
    teacher = "Не указан"
    location = "Не указана"
    
    # Поиск преподавателя и аудитории
    if len(lines) > 1:
        # Пытаемся найти преподавателя, который часто идет второй строкой
        if lines[1].istitle() or re.match(r'[А-ЯЁ]\.\s*[А-ЯЁ]\.\s*[А-ЯЁ][а-яё]+', lines[1]):
            teacher = lines[1]
            if len(lines) > 2:
                location = " ".join(lines[2:])
        else:
            # Если вторая строка не похожа на ФИО, считаем ее аудиторией
            location = " ".join(lines[1:])
            
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

# --- ОСНОВНАЯ ЛОГИКА ---
def main():
    if not os.path.exists(SCHEDULES_DIR):
        print(f"Ошибка: Директория для поиска расписаний '{SCHEDULES_DIR}' не найдена. Возможно, скрапинг не был запущен или завершился ошибкой.")
        sys.exit(1)
        
    # 1. Удаляем старую БД (для гарантии свежести)
    if os.path.exists(DB_PATH):
        print(f"Удаление старой базы данных '{os.path.basename(DB_PATH)}'...")
        os.remove(DB_PATH)
        
    conn = sqlite3.connect(DB_PATH)
    
    # 2. !!! КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Создаем структуру таблиц !!!
    # Таблицы должны быть созданы, прежде чем мы попытаемся в них что-то писать/удалять.
    create_db_tables(conn)

    cursor = conn.cursor()

    # 3. Очистка старых данных перед записью новых (ХОТЯ МЫ УЖЕ УДАЛИЛИ ФАЙЛ, 
    # этот DELETE может быть полезен, если мы решим не удалять файл БД в будущем. 
    # В текущей логике это избыточно, но безопасно, т.к. таблица существует.)
    logging.info("Очистка старых данных из таблицы 'schedule'...")
    cursor.execute("DELETE FROM schedule;")
    conn.commit()
    
    all_lessons_to_insert = []

    print("Начинаем обработку файлов расписания...")
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
            if not filename.endswith((".xls", ".xlsx")):
                continue
            
            file_path = os.path.join(dirpath, filename)
            logging.info(f"    - Файл: {os.path.basename(file_path)}")
            week_type = determine_week_type(filename)
            if week_type == 'неизвестно':
                logging.warning(f"      ПРЕДУПРЕЖДЕНИЕ: Не удалось определить тип недели для файла. Пропуск.")
                continue
            
            try:
                # Чтение с автоматическим определением первой строки заголовка
                df = pd.read_excel(file_path, header=None)
            except Exception as e:
                logging.error(f"      Ошибка чтения файла: {e}. Пропуск.")
                continue

            # Поиск строки с заголовком 'День'
            header_row_index = -1
            header_mapping = {} # Для обработки столбцов с разными названиями
            
            for i, row in df.iterrows():
                row_str = [str(cell) for cell in row.tolist()]
                
                # Ищем стандартные заголовки
                if 'День' in row_str:
                    header_row_index = i
                    break
                # Или ищем альтернативные, если таблица на английском или имеет другой формат
                if 'Day' in row_str and 'Time' in row_str:
                    header_row_index = i
                    header_mapping = {
                        'День': 'Day',
                        'Часы': 'Time'
                    }
                    break

            if header_row_index == -1:
                logging.warning("      Не найден заголовок таблицы ('День'). Пропуск.")
                continue

            # Повторное чтение с правильным заголовком
            df = pd.read_excel(file_path, header=header_row_index)
            # Приведение имен столбцов к строковому формату
            df.columns = [str(col).strip() for col in df.columns]

            # Определяем фактические названия столбцов для Дня и Часов
            day_col_name = 'День' if 'День' in df.columns else ('Day' if 'Day' in df.columns else None)
            time_col_name = 'Часы' if 'Часы' in df.columns else ('Time' if 'Time' in df.columns else None)

            if not day_col_name or not time_col_name:
                 print("      Не найдены ключевые столбцы ('День'/'Часы'). Пропуск.")
                 continue

            # Определяем столбцы, которые являются группами
            groups = [col for col in df.columns if col not in [day_col_name, time_col_name, 'nan', 'Unnamed: 0']]
            current_date_str = None
            
            for index, row in df.iterrows():
                potential_date = parse_date_from_cell(str(row.get(day_col_name)), CURRENT_YEAR)
                if potential_date:
                    current_date_str = potential_date
                    
                if not current_date_str: continue
                    
                time_slot = str(row.get(time_col_name, '')).strip()
                if not time_slot or "nan" in time_slot:
                    continue
                    
                for group in groups:
                    lesson_info = parse_lesson_cell(row.get(group))
                    if lesson_info:
                        # Структура: group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course
                        all_lessons_to_insert.append((
                            str(group).strip(), current_date_str, time_slot,
                            lesson_info['subject'], lesson_info['teacher'], lesson_info['location'],
                            week_type, faculty, course
                        ))

    if not all_lessons_to_insert:
        print("\nНе было найдено ни одной пары для добавления. База данных создана, но пуста.")
        conn.close()
        return

    logging.info(f"\nЗапись {len(all_lessons_to_insert)} пар в базу данных...")
    cursor.executemany("""
    INSERT INTO schedule (group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_lessons_to_insert)
    conn.commit()
    conn.close()
    
    logging.info(f"\nГотово! База данных '{os.path.basename(DB_PATH)}' успешно обновлена.")

if __name__ == "__main__":
    main()