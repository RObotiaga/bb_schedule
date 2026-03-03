"""
Расчёт статистики преподавателей: процент закрываемости по предметам.
Использует маппинг кластер → группа для связи обезличенных зачёток с расписанием.
"""
import json
import logging
import re
from collections import defaultdict

from app.core.database import (
    get_schedule_teacher_subjects,
    get_all_cluster_groups,
    get_cluster_students_subjects,
    save_teacher_stat,
    clear_teacher_stats,
)


def _find_subject_result(subjects_data: list, target_subject: str) -> dict | None:
    """
    Ищет предмет в списке оценок студента.
    Сравнивает названия нечётко — предмет из расписания может немного
    отличаться от предмета в зачётке (сокращения, регистр и т.д.).
    """
    target_clean = target_subject.strip().lower()

    for item in subjects_data:
        subj = item.get("subject", "").strip().lower()
        # Точное совпадение
        if subj == target_clean:
            return item
        # Одно название содержит другое (для случаев вроде "Математика" vs "Математика (экзамен)")
        if target_clean in subj or subj in target_clean:
            return item

    return None


async def calculate_teacher_stats():
    """
    Полный расчёт статистики преподавателей.
    Для каждой тройки (преподаватель, предмет, группа) из расписания
    считает процент студентов, успешно сдавших этот предмет.
    """
    # Очищаем старые данные перед пересчётом
    await clear_teacher_stats()

    # Получаем маппинг группа → cluster_id
    cluster_groups = await get_all_cluster_groups()
    group_to_cluster = {cg["group_name"]: cg["cluster_id"] for cg in cluster_groups}

    if not group_to_cluster:
        logging.warning("Нет маппинга кластер→группа, статистика невозможна")
        return

    # Получаем все тройки (преподаватель, предмет, группа) из расписания
    teacher_subjects = await get_schedule_teacher_subjects()
    if not teacher_subjects:
        logging.warning("Нет данных расписания для расчёта статистики")
        return

    # Кэшируем данные студентов по кластерам (чтобы не грузить повторно)
    cluster_cache: dict[int, list] = {}

    stats_count = 0
    for entry in teacher_subjects:
        teacher = entry["teacher"]
        subject = entry["subject"]
        group_name = entry["group_name"]

        cluster_id = group_to_cluster.get(group_name)
        if cluster_id is None:
            continue

        # Загружаем данные студентов кластера
        if cluster_id not in cluster_cache:
            students = await get_cluster_students_subjects(cluster_id)
            # Парсим JSON один раз
            parsed = []
            for student in students:
                try:
                    subj_list = json.loads(student["subjects_json"])
                    parsed.append(subj_list)
                except (json.JSONDecodeError, KeyError):
                    continue
            cluster_cache[cluster_id] = parsed

        students_data = cluster_cache[cluster_id]
        if not students_data:
            continue

        # Считаем статистику по предмету
        total = 0
        passed = 0
        academic_year = ""

        for student_subjects in students_data:
            result = _find_subject_result(student_subjects, subject)
            if result is None:
                continue

            total += 1
            if result.get("passed", False):
                passed += 1

            # Берём учебный год из семестра предмета
            if not academic_year:
                semester = result.get("semester", "")
                year_match = re.search(r'(\d{4}/\d{4})', semester)
                if year_match:
                    academic_year = year_match.group(1)

        # Сохраняем только если есть хотя бы один студент с этим предметом
        if total > 0:
            pass_rate = round(passed / total * 100, 1)
            await save_teacher_stat(
                teacher, subject, group_name,
                total, passed, pass_rate,
                academic_year or "Неизвестно",
            )
            stats_count += 1

    logging.info(f"Статистика преподавателей: рассчитано {stats_count} записей")
