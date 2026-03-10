"""
Автоматическая кластеризация студентов по набору предметов
и определение отчисленных.
"""
import json
import logging
import re
from collections import defaultdict
from typing import List, Dict

from app.core.repositories.rating import get_all_rating_records, update_rating_cluster, save_expelled_student

# Минимальный процент совпадения предметов для объединения в один кластер
SIMILARITY_THRESHOLD = 0.80

# Текущий учебный год — для определения отчисленных
CURRENT_ACADEMIC_YEAR = "2025/2026"


def _extract_subject_set(subjects_json: str) -> set:
    """Извлекает множество уникальных названий предметов из JSON."""
    try:
        subjects = json.loads(subjects_json)
        return {item["subject"] for item in subjects if item.get("subject")}
    except (json.JSONDecodeError, KeyError):
        return set()


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Коэффициент Жаккара — мера сходства двух множеств."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _has_current_year_subjects(subjects_json: str) -> bool:
    """Проверяет есть ли предметы за текущий учебный год."""
    try:
        subjects = json.loads(subjects_json)
        return any(
            CURRENT_ACADEMIC_YEAR in item.get("semester", "")
            for item in subjects
        )
    except (json.JSONDecodeError, KeyError):
        return False


def cluster_students(records: List[dict], base_year: int = 0) -> Dict[str, int]:
    """
    Группирует студентов по набору предметов.
    Использует жадный алгоритм: берём первого неразмеченного студента,
    создаём кластер, добавляем всех похожих.
    
    Returns: {record_book: cluster_id}
    """
    # Извлекаем множества предметов
    student_subjects = {}
    for rec in records:
        subjects = _extract_subject_set(rec["subjects_json"])
        if subjects:  # Пропускаем пустые
            student_subjects[rec["record_book"]] = subjects

    assignments = {}
    cluster_id = (base_year * 1000) if base_year > 0 else 0
    assigned = set()

    # Сортируем по количеству предметов (больше предметов = лучший представитель кластера)
    sorted_books = sorted(
        student_subjects.keys(),
        key=lambda rb: len(student_subjects[rb]),
        reverse=True,
    )

    for book in sorted_books:
        if book in assigned:
            continue

        # Новый кластер с этим студентом как центроидом
        cluster_id += 1
        centroid = student_subjects[book]
        assignments[book] = cluster_id
        assigned.add(book)

        # Ищем похожих студентов
        for other_book in sorted_books:
            if other_book in assigned:
                continue
            similarity = _jaccard_similarity(centroid, student_subjects[other_book])
            if similarity >= SIMILARITY_THRESHOLD:
                assignments[other_book] = cluster_id
                assigned.add(other_book)

    logging.info(f"Кластеризация: {len(assignments)} студентов → {cluster_id} кластеров")
    return assignments


def detect_expelled(records: List[dict], clusters: Dict[str, int]) -> Dict[str, bool]:
    """
    Определяет отчисленных студентов по комбинированному критерию:
    1. Нет предметов за текущий учебный год
    2. Количество предметов значительно ниже медианы кластера (< 50%)
    
    Returns: {record_book: is_expelled}
    """
    # Считаем медиану предметов по кластерам
    cluster_counts = defaultdict(list)
    record_map = {r["record_book"]: r for r in records}

    for book, cid in clusters.items():
        rec = record_map.get(book)
        if rec:
            cluster_counts[cid].append(rec.get("total_subjects", 0))

    cluster_medians = {}
    for cid, counts in cluster_counts.items():
        sorted_counts = sorted(counts)
        mid = len(sorted_counts) // 2
        cluster_medians[cid] = sorted_counts[mid] if sorted_counts else 0

    expelled = {}
    for rec in records:
        book = rec["record_book"]
        cid = clusters.get(book)
        total = rec.get("total_subjects", 0)

        # Критерий 1: нет предметов за текущий год
        has_current = _has_current_year_subjects(rec.get("subjects_json", "[]"))

        # Критерий 2: количество предметов < 50% медианы кластера
        median = cluster_medians.get(cid, 0) if cid else 0
        below_median = total < median * 0.5 if median > 0 else False

        # Комбо: оба критерия должны совпасть
        expelled[book] = not has_current and below_median

    expelled_count = sum(1 for v in expelled.values() if v)
    logging.info(f"Определение отчислений: {expelled_count} из {len(expelled)} отчислены")
    return expelled


async def run_clustering(enrollment_year: int = 2022):
    """
    Полный цикл кластеризации: загрузка данных → кластеризация → определение отчисленных → сохранение.
    """
    records = await get_all_rating_records(enrollment_year)
    if not records:
        logging.warning("Нет данных для кластеризации")
        return

    clusters = cluster_students(records, base_year=enrollment_year)
    expelled = detect_expelled(records, clusters)

    # Сохраняем результаты в БД
    for rec in records:
        book = rec["record_book"]
        cid = clusters.get(book, 0)
        is_exp = expelled.get(book, False)
        
        if is_exp:
            await save_expelled_student(book, enrollment_year, cid)
        else:
            await update_rating_cluster(book, cid, 0)

    logging.info(f"Кластеризация завершена для {enrollment_year} года")
