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

    # Для глобальной статистики
    # предмет -> {зачётка -> сдал_хотя_бы_раз}
    global_student_map = defaultdict(lambda: defaultdict(bool))
    # предмет -> {всего_записей, сдано_записей}
    global_entry_stats = defaultdict(lambda: {"total": 0, "passed": 0})

    # Для кластерной статистики
    # кластер -> предмет -> {зачётка -> сдал_хотя_бы_раз}
    cluster_student_map = defaultdict(lambda: defaultdict(lambda: defaultdict(bool)))
    # кластер -> предмет -> {всего_записей, сдано_записей}
    cluster_entry_stats = defaultdict(lambda: defaultdict(lambda: {"total": 0, "passed": 0}))

    for record in records:
        if record.get("is_expelled", 0) == 1:
            continue

        try:
            subjects = json.loads(record["subjects_json"])
        except (json.JSONDecodeError, KeyError):
            continue

        rb = record["record_book"]
        cluster_id = record.get("cluster_id")

        for item in subjects:
            raw_subj = item.get("subject")
            if not raw_subj:
                continue
            
            subj_name = raw_subj.strip()
            if not subj_name:
                continue
                
            is_passed = item.get("passed", False)
            
            # 1. Глобально (по записям)
            global_entry_stats[subj_name]["total"] += 1
            if is_passed:
                global_entry_stats[subj_name]["passed"] += 1
            
            # 2. Глобально (по людям)
            global_student_map[subj_name][rb] = global_student_map[subj_name][rb] or is_passed

            # 3. В кластере
            if cluster_id:
                cluster_entry_stats[cluster_id][subj_name]["total"] += 1
                if is_passed:
                    cluster_entry_stats[cluster_id][subj_name]["passed"] += 1
                
                cluster_student_map[cluster_id][subj_name][rb] = cluster_student_map[cluster_id][subj_name][rb] or is_passed

    # Сохраняем глобальную статистику
    global_count = 0
    for subj_name, entries in global_entry_stats.items():
        # Статистика по записям
        total_e = entries["total"]
        passed_e = entries["passed"]
        rate_e = round(passed_e / total_e * 100, 1) if total_e > 0 else 0.0

        # Статистика по людям
        students = global_student_map[subj_name]
        total_p = len(students)
        passed_p = sum(1 for p_passed in students.values() if p_passed)
        rate_p = round(passed_p / total_p * 100, 1) if total_p > 0 else 0.0

        await save_subject_global_stat(
            subj_name, total_e, passed_e, rate_e,
            total_persons=total_p, passed_persons=passed_p, person_pass_rate=rate_p
        )
        global_count += 1
            
    # Сохраняем кластерную статистику
    cluster_count = 0
    for cid, subjects in cluster_entry_stats.items():
        for subj_name, entries in subjects.items():
            # Записи
            total_e = entries["total"]
            passed_e = entries["passed"]
            rate_e = round(passed_e / total_e * 100, 1) if total_e > 0 else 0.0

            # Люди
            students = cluster_student_map[cid][subj_name]
            total_p = len(students)
            passed_p = sum(1 for p_passed in students.values() if p_passed)
            rate_p = round(passed_p / total_p * 100, 1) if total_p > 0 else 0.0

            await save_cluster_subject_stat(
                cid, subj_name, total_e, passed_e, rate_e,
                total_persons=total_p, passed_persons=passed_p, person_pass_rate=rate_p
            )
            cluster_count += 1

    logging.info(f"Статистика по предметам обновлена: {global_count} глобальных, {cluster_count} кластерных записей (учтено по людям)")

