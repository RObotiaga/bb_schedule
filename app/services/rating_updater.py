"""
Фоновая задача: массовый парсинг зачёток и обновление рейтинга.
Запускается раз в сутки через scheduler.
"""
import json
import logging
import re

from app.core.database import save_rating_record
from app.services.rating_scraper import scrape_all_records
from app.services.clustering import run_clustering


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
    """
    logging.info("🏆 Начало обновления рейтинга...")

    # Шаг 1: Массовый парсинг
    stats = await scrape_all_records(
        year=2022,
        start=1,
        end=1523,
        delay_range=(2, 8),
        on_result=_on_record_parsed,
    )
    logging.info(f"📊 Парсинг завершён: {stats}")

    # Шаг 2: Кластеризация и определение отчисленных
    await run_clustering(enrollment_year=2022)

    logging.info("✅ Обновление рейтинга завершено")
