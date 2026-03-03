"""
Маппинг кластеров (из rating_data) на реальные группы расписания.
Сопоставляет множества предметов кластера с предметами группы по Жаккару.
"""
import logging
from app.core.database import (
    get_all_distinct_clusters,
    get_cluster_subjects,
    get_schedule_groups_subjects,
    save_cluster_group,
)

# Минимальное сходство для привязки кластера к группе
MIN_SIMILARITY = 0.25


def _jaccard(set_a: set, set_b: set) -> float:
    """Коэффициент Жаккара между двумя множествами."""
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


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

        for group_name, group_subj in group_subjects.items():
            sim = _jaccard(cluster_subj, group_subj)
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
