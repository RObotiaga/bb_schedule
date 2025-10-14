import pandas as pd
import os
import sqlite3
import re
from datetime import datetime

# --- КОНФИГУРАЦИЯ ---
SCHEDULES_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "schedules"))
DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "schedule.db"))
CURRENT_YEAR = datetime.now().year

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
MONTHS_MAP = {
    'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
    'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
}

def determine_week_type(filename):
    """Определяет тип недели по имени файла."""
    filename_lower = filename.lower()
    if 'нечетная' in filename_lower or 'нечет' in filename_lower:
        return 'нечетная'
    if 'четная' in filename_lower or 'чет' in filename_lower:
        return 'четная'
    return 'неизвестно'

def parse_date_from_cell(cell_content, year):
    """Извлекает дату из ячейки и возвращает ее в формате YYYY-MM-DD."""
    if not isinstance(cell_content, str):
        return None
    match = re.search(r'(\d+)\s+([а-я]+)', cell_content, re.IGNORECASE)
    if match:
        day = int(match.group(1))
        month_str = match.group(2).lower()
        month = MONTHS_MAP.get(month_str)
        if month:
            try:
                return datetime(year, month, day).strftime('%Y-%m-%d')
            except ValueError:
                return None
    return None

def parse_lesson_cell(cell_content):
    """Парсит информацию о паре из одной ячейки."""
    if not isinstance(cell_content, str) or cell_content.strip() == "":
        return None
    lines = [re.sub(r'^-', '', line).strip() for line in cell_content.split('\n') if line.strip()]
    if not lines:
        return None
        
    subject = lines[0]
    teacher = "Не указан"
    location = "Не указана"
    if len(lines) > 1:
        teacher = lines[1]
    if len(lines) > 2:
        location = lines[2]
        
    subgroup_info = ""
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
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_group_date ON schedule (group_name, lesson_date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_teacher_date ON schedule (teacher, lesson_date)")
    conn.commit()

# --- ОСНОВНАЯ ЛОГИКА ---
def main():
    """
    Главная функция: полностью пересоздает базу данных schedule.db,
    парсит все XLS файлы и наполняет ее свежими данными.
    """
    # --- ЛОГИКА ПОЛНОГО ОБНОВЛЕНИЯ ---
    # Удаляем старую БД, чтобы гарантировать 100% свежесть данных
    if os.path.exists(DB_PATH):
        print(f"Удаление старой базы данных '{os.path.basename(DB_PATH)}'...")
        os.remove(DB_PATH)
    # ------------------------------------
        
    conn = sqlite3.connect(DB_PATH)
    create_db_tables(conn)
    
    all_lessons_to_insert = []

    if not os.path.exists(SCHEDULES_DIR):
        print(f"Ошибка: Директория для поиска расписаний '{SCHEDULES_DIR}' не найдена.")
        conn.close()
        return

    print("Начинаем обработку файлов расписания...")
    for dirpath, _, filenames in os.walk(SCHEDULES_DIR):
        if dirpath == SCHEDULES_DIR:
            continue
            
        relative_path = os.path.relpath(dirpath, SCHEDULES_DIR)
        path_parts = relative_path.split(os.sep)
        faculty = path_parts[0] if len(path_parts) > 0 else "Неизвестно"
        course_str = path_parts[1] if len(path_parts) > 1 else "Без курса"
        course_match = re.search(r'\d+', course_str)
        course = course_match.group(0) if course_match else "N/A"
        
        print(f"\n--- Обработка папки: {faculty} / {course_str} ---")

        for filename in filenames:
            if not filename.endswith((".xls", ".xlsx")):
                continue
            
            file_path = os.path.join(dirpath, filename)
            print(f"    - Файл: {os.path.basename(file_path)}")
            week_type = determine_week_type(filename)
            if week_type == 'неизвестно':
                print(f"      ПРЕДУПРЕЖДЕНИЕ: Не удалось определить тип недели для файла. Пропуск.")
                continue
            
            try:
                df = pd.read_excel(file_path, header=None)
            except Exception as e:
                print(f"      Ошибка чтения файла: {e}. Пропуск.")
                continue

            header_row_index = -1
            for i, row in df.iterrows():
                if 'День' in str(row.iloc[0]):
                    header_row_index = i
                    break
            if header_row_index == -1:
                print("      Не найден заголовок таблицы ('День'). Пропуск.")
                continue

            df = pd.read_excel(file_path, header=header_row_index)
            groups = [col for col in df.columns if col not in ['День', 'Часы']]
            current_date_str = None
            
            for index, row in df.iterrows():
                potential_date = parse_date_from_cell(str(row.get('День')), CURRENT_YEAR)
                if potential_date:
                    current_date_str = potential_date
                    
                if not current_date_str:
                    continue
                    
                time_slot = str(row.get('Часы', '')).strip()
                if not time_slot or "nan" in time_slot:
                    continue
                    
                for group in groups:
                    lesson_info = parse_lesson_cell(row.get(group))
                    if lesson_info:
                        all_lessons_to_insert.append((
                            str(group).strip(), current_date_str, time_slot,
                            lesson_info['subject'], lesson_info['teacher'], lesson_info['location'],
                            week_type, faculty, course
                        ))

    if not all_lessons_to_insert:
        print("\nНе было найдено ни одной пары для добавления. База данных осталась пустой.")
        conn.close()
        return

    print(f"\nЗапись {len(all_lessons_to_insert)} пар в базу данных...")
    cursor = conn.cursor()
    cursor.executemany("""
    INSERT INTO schedule (group_name, lesson_date, time, subject, teacher, location, week_type, faculty, course)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, all_lessons_to_insert)
    conn.commit()
    conn.close()
    
    print(f"\nГотово! База данных '{os.path.basename(DB_PATH)}' успешно создана и наполнена с нуля.")

if __name__ == "__main__":
    main()