"""
Фоновая задача: массовый парсинг зачёток и обновление рейтинга.
Запускается раз в сутки через scheduler.
"""
import json
import logging
import re
from datetime import datetime

from app.core.database import save_rating_record, save_job_log, cleanup_old_job_logs
from app.services.rating_scraper import scrape_all_records
from app.services.clustering import run_clustering
from app.services.cluster_mapper import map_clusters_to_groups
from app.services.teacher_stats import calculate_teacher_stats


def _compute_stats(data: list) -> dict:
    """Считает статистику из списка предметов."""
    total = len(data)
    passed = sum(1 for item in data if item.get("passed", False))
    pass_rate = (passed / total * 100) if total > 0 else 0.0

    # Последний учебный год — ищем максимальный
    years = set()
    for item in data:
        semester = item.get("semester", "")
        match = re.search(r'(\d{4}/\d{4})', semester)
        if match:
            years.add(match.group(1))

    last_year = max(years) if years else ""
    return {
        "total": total,
        "passed": passed,
        "pass_rate": round(pass_rate, 2),
        "last_academic_year": last_year,
    }


async def _on_record_parsed(record_book: str, status: str, data: list | None):
    """Callback: сохраняет результат парсинга одной зачётки в БД."""
    if status != "SUCCESS" or not data:
        return

    # Год зачисления — первые 4 цифры номера зачётки
    enrollment_year = int(record_book[:4])
    stats = _compute_stats(data)

    await save_rating_record(
        record_book=record_book,
        enrollment_year=enrollment_year,
        subjects_json=json.dumps(data, ensure_ascii=False),
        total_subjects=stats["total"],
        passed_subjects=stats["passed"],
        pass_rate=stats["pass_rate"],
        last_academic_year=stats["last_academic_year"],
    )


async def run_rating_update():
    """
    Полный цикл обновления рейтинга:
    1. Парсинг всех зачёток 2022 года
    2. Кластеризация
    3. Маппинг кластеров на группы расписания
    4. Расчёт статистики преподавателей
    """
    start_time = datetime.now()
    logging.info(f"🏆 Начало обновления рейтинга: {start_time}...")
    
    details = {}
    status = "ERROR"

    try:
        # Шаг 1: Массовый парсинг
        stats = await scrape_all_records(
            year=2022,
            start=1,
            end=1523,
            delay_range=(2, 8),
            on_result=_on_record_parsed,
        )
        logging.info(f"📊 Парсинг завершён: {stats}")
        
        details.update(stats)

        # Шаг 2: Кластеризация и определение отчисленных
        await run_clustering(enrollment_year=2022)

        # Шаг 3: Маппинг кластеров на группы расписания
        await map_clusters_to_groups()
        logging.info("🗺️ Маппинг кластеров завершён")

        # Шаг 4: Расчёт статистики преподавателей
        await calculate_teacher_stats()
        logging.info("📊 Статистика преподавателей рассчитана")
        
        status = "SUCCESS"
        logging.info("✅ Обновление рейтинга завершено")
        
    except Exception as e:
        status = "ERROR"
        details["error"] = str(e)
        logging.exception("Ошибка при обновлении рейтинга")
        
    finally:
        end_time = datetime.now()
        duration = end_time - start_time
        details["duration_seconds"] = duration.total_seconds()
        
        try:
            await save_job_log("rating_update", start_time, end_time, status, details)
            await cleanup_old_job_logs(days=30)
        except Exception as e_log:
            logging.error(f"Не удалось сохранить лог задачи: {e_log}")
