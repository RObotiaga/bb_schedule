"""
Маппинг кластеров (из rating_data) на реальные группы расписания.
Сопоставляет множества предметов кластера с предметами группы по Жаккару.
"""
import logging
from app.core.repositories.rating import (
    get_all_rating_records,
    get_cluster_subjects,
    get_schedule_groups_subjects,
    save_cluster_group,
    get_cluster_size,
)

# Минимальное сходство для привязки кластера к группе
MIN_SIMILARITY = 0.25


import re

def _similarity(cluster_subjects: set, group_subjects: set) -> float:
    """Точность вхождения текущих предметов группы в полный список предметов кластера."""
    if not cluster_subjects or not group_subjects:
        return 0.0
    
    # Очищаем предметы из расписания от подгрупп "(1п/г)" 
    cleaned_group_subjects = set()
    for s in group_subjects:
        cleaned = re.sub(r'\s*\(\d+\s*п/г\)', '', s).strip()
        cleaned_group_subjects.add(cleaned)
    
    cleaned_cluster_subjects = set()
    for s in cluster_subjects:
        cleaned = re.sub(r'\s*\(\d+\s*п/г\)', '', s).strip()
        cleaned_cluster_subjects.add(cleaned)

    intersection = len(cleaned_cluster_subjects & cleaned_group_subjects)
    # Метрика поглощения: какой % предметов расписания есть у этих студентов?
    inclusion = intersection / len(cleaned_group_subjects) if cleaned_group_subjects else 0.0
    
    # Небольшой штраф по Жаккару для разрешения спорных случаев между курсами
    union = len(cleaned_cluster_subjects | cleaned_group_subjects)
    jaccard = intersection / union if union > 0 else 0.0
    
    return inclusion + (jaccard * 0.1)


async def map_clusters_to_groups():
    """
    Для каждого кластера ищет ближайшую группу расписания.
    Сохраняет результат в cluster_groups.
    """
    cluster_ids = await get_all_distinct_clusters()
    if not cluster_ids:
        logging.warning("Нет кластеров для маппинга")
        return

    # Собираем предметы каждой группы расписания
    group_subjects = await get_schedule_groups_subjects()
    if not group_subjects:
        logging.warning("Нет данных расписания для маппинга")
        return

    mapped = 0
    for cluster_id in cluster_ids:
        cluster_subj = await get_cluster_subjects(cluster_id)
        if not cluster_subj:
            continue

        # Ищем группу с максимальным сходством
        best_group = None
        best_sim = 0.0

        for group_name, group_data in group_subjects.items():
            group_course = group_data["course"]
            group_subj = group_data["subjects"]
            
            # В текущем учебном году (2025/2026), 
            # курс студента = 2026 - год поступления
            # Если cluster_id = 2022001 -> год поступления = 2022 -> курс = 4
            expected_course = 2026 - (cluster_id // 1000)
            
            if group_course != expected_course:
                continue

            sim = _similarity(cluster_subj, group_subj)
            if sim > best_sim:
                best_sim = sim
                best_group = group_name

        if best_group and best_sim >= MIN_SIMILARITY:
            await save_cluster_group(cluster_id, best_group, round(best_sim, 3))
            mapped += 1
            logging.debug(
                f"Кластер #{cluster_id} → {best_group} (сходство {best_sim:.2f})"
            )

    logging.info(
        f"Маппинг кластеров: {mapped}/{len(cluster_ids)} привязано к группам"
    )
