import re
import math

def get_course_from_semester(semester_str: str) -> str:
    match = re.search(r'(\d+)\s*семестр', semester_str.lower())
    if match:
        sem_num = int(match.group(1))
        course_num = math.ceil(sem_num / 2)
        return f"{course_num} курс"
    return "Остальное"

def filter_results_by_settings(data: list, settings: dict) -> list:
    filtered = []
    for item in data:
        if settings.get("hide_5") and item.get('grade_value') == 5: continue
        if settings.get("hide_4") and item.get('grade_value') == 4: continue
        if settings.get("hide_3") and item.get('grade_value') == 3: continue
        if settings.get("hide_2") and item.get('grade_value') == 2: continue
        if settings.get("hide_passed_non_exam") and item.get('passed') and item.get('grade_value') is None: continue
        if settings.get("hide_failed") and not item.get('passed'): continue
        filtered.append(item)
    return filtered

def escape_md(text: str) -> str:
    """Escape MarkdownV1 reserved characters: _ * ` [."""
    for char in ('_', '*', '`', '['):
        text = text.replace(char, f'\\{char}')
    return text

def format_results(data: list, settings: dict = None, rating_info: dict | None = None, subject_stats: dict | None = None, cluster_subject_stats: dict | None = None, teacher_map: dict | None = None) -> str:
    if settings is None:
        settings = {}
    if subject_stats is None:
        subject_stats = {}
    if cluster_subject_stats is None:
        cluster_subject_stats = {}
    if teacher_map is None:
        teacher_map = {}
    if not data: return "📭 Результаты не найдены."
    
    filtered_data = filter_results_by_settings(data, settings)
    if not filtered_data: return "📭 Все предметы скрыты настройками фильтрации."

    courses = {}
    for item in filtered_data:
        sem = item.get('semester', '')
        if 'course' in item and item['course']:
            course = f"{item['course']} курс"
        else:
            course = get_course_from_semester(sem)
            
        if course not in courses: courses[course] = {}
        if sem not in courses[course]: courses[course][sem] = []
        courses[course][sem].append(item)
    
    output = []
    
    def extract_num(text):
        match = re.search(r'\d+', text)
        return int(match.group(0)) if match else 999
        
    sorted_courses = sorted(courses.keys(), key=extract_num)
    
    # Статистика по ВСЕМ предметам (без фильтров) для объективного показателя
    all_total = len(data)
    all_passed = sum(1 for item in data if item['passed'])
    all_pass_rate = (all_passed / all_total * 100) if all_total > 0 else 0
    
    # Статистика по отфильтрованным (для отображения долгов)
    debts = sum(1 for item in filtered_data if not item['passed'])
    
    output.append("📊 *Сводка*")
    output.append(f"Всего предметов: {all_total}")
    output.append(f"Закрыто: {all_passed}/{all_total} ({all_pass_rate:.1f}%)")
    output.append(f"Долгов: {debts}")
    
    # Место в рейтинге (если данные доступны)
    if rating_info:
        def get_better_than_text(pos, total):
            if total > 1:
                percent = int(round((total - pos) / total * 100))
                return f" (Лучше чем {percent}% учеников!)"
            return ""

        if "cluster_pos" in rating_info:
            pos, total = rating_info["cluster_pos"]
            output.append(f"📍 Место среди специальности: {pos} из {total}{get_better_than_text(pos, total)}")
        if "year_pos" in rating_info:
            pos, total = rating_info["year_pos"]
            output.append(f"📍 Место среди всех поступивших за год: {pos} из {total}{get_better_than_text(pos, total)}")
        if "all_pos" in rating_info:
            pos, total = rating_info["all_pos"]
            output.append(f"📍 Место за все года: {pos} из {total}{get_better_than_text(pos, total)}")
    
    output.append("")
    
    def sem_sort_key(s):
        year_m = re.search(r'(\d{4})/\d{4}', s)
        year = int(year_m.group(1)) if year_m else 0
        sem_m = re.search(r'(\d+)\s*семестр', s)
        sem = int(sem_m.group(1)) if sem_m else 999
        return (year, sem)
    
    for course in sorted_courses:
        output.append(f"\n🎓 *{escape_md(course)}*")
        
        sorted_sems = sorted(courses[course].keys(), key=sem_sort_key)
        for sem in sorted_sems:
            semester_lines = []
            for item in courses[course][sem]:
                icon = "✅" if item['passed'] else "⚠️"
                if not item['passed']: icon = "❌"
                if "неудовл" in item['grade'].lower(): icon = "❌"
                safe_subject = escape_md(item['subject'])
                line = f"{icon} *{safe_subject}*\n   🔹 {escape_md(item['grade'])}"
                
                # Check for global subject stats
                subj_name = item['subject'].strip()
                if subj_name in cluster_subject_stats:
                    c_pass_rate = cluster_subject_stats[subj_name]
                    line += f"  |  👥 В группе: {c_pass_rate}%"
                if subj_name in subject_stats:
                    pass_rate = subject_stats[subj_name]
                    line += f"  |  🌍 Глобально: {pass_rate}%"
                
                # Преподаватели из расписания
                if subj_name in teacher_map and teacher_map[subj_name]:
                    # Сокращаем ФИО: "Першин Виталий Константинович, Профессор" → "Першин В.К."
                    short_names = []
                    for full in teacher_map[subj_name]:
                        # Убираем должность после запятой
                        name_part = full.split(',')[0].strip()
                        parts = name_part.split()
                        if len(parts) >= 3:
                            short_names.append(f"{parts[0]} {parts[1][0]}.{parts[2][0]}.")
                        elif len(parts) == 2:
                            short_names.append(f"{parts[0]} {parts[1][0]}.")
                        else:
                            short_names.append(name_part)
                    line += f"\n   👨\u200d🏫 {escape_md(', '.join(short_names))}"
                if item['date']: line += f" ({escape_md(item['date'])})"
                semester_lines.append(line)
            
            if semester_lines:
                output.append(f"📅 _{sem}_")
                output.extend(semester_lines)
            
    return "\n".join(output)
