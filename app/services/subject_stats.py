"""
Расчёт статистики предметов: глобальный процент закрываемости.
"""
import json
import logging
from collections import defaultdict
from app.core.database import (
    get_all_rating_records,
    save_subject_global_stat,
    clear_subject_global_stats,
    save_cluster_subject_stat,
)

async def calculate_subject_stats():
    """
    Полный расчёт глобальной статистики закрываемости предметов.
    """
    # Очищаем старые данные перед пересчётом
    await clear_subject_global_stats()

    # Получаем все записи (включаем все года)
    records = await get_all_rating_records()
    if not records:
        logging.warning("Нет записей в rating_data для расчёта статистики")
        return

    subject_data = defaultdict(lambda: {"total": 0, "passed": 0})
    cluster_subject_data = defaultdict(lambda: defaultdict(lambda: {"total": 0, "passed": 0}))

    for record in records:
        try:
            subjects = json.loads(record["subjects_json"])
        except (json.JSONDecodeError, KeyError):
            continue

        for item in subjects:
            raw_subj = item.get("subject")
            if not raw_subj:
                continue
            
            subj_name = raw_subj.strip()
            # Пропускаем пустые или кривые названия
            if not subj_name:
                continue
                
            subject_data[subj_name]["total"] += 1
            is_passed = item.get("passed", False)
            if is_passed:
                subject_data[subj_name]["passed"] += 1
                
            cluster_id = record.get("cluster_id")
            if cluster_id:
                cluster_subject_data[cluster_id][subj_name]["total"] += 1
                if is_passed:
                    cluster_subject_data[cluster_id][subj_name]["passed"] += 1

    stats_count = 0
    for subj_name, data in subject_data.items():
        total = data["total"]
        if total > 0:
            passed = data["passed"]
            pass_rate = round(passed / total * 100, 1)
            await save_subject_global_stat(subj_name, total, passed, pass_rate)
            stats_count += 1
            
    cluster_stats_count = 0
    for cluster_id, subjects in cluster_subject_data.items():
        for subj_name, data in subjects.items():
            total = data["total"]
            if total > 0:
                passed = data["passed"]
                pass_rate = round(passed / total * 100, 1)
                await save_cluster_subject_stat(cluster_id, subj_name, total, passed, pass_rate)
                cluster_stats_count += 1

    logging.info(f"Статистика по предметам: рассчитано {stats_count} глобальных, {cluster_stats_count} кластерных записей")
