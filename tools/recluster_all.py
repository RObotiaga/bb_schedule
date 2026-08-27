import asyncio
import logging
import sys
import os

# Добавляем путь к приложению
sys.path.append(os.getcwd())

from app.core.config import PARSING_YEARS
from app.services.clustering import run_clustering
from app.services.cluster_mapper import map_clusters_to_groups
from app.services.subject_stats import calculate_subject_stats
from app.core.database import initialize_database, close_db_connection

async def recluster_all():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Гарантированно закрываем БД при любом исходе: не-daemon воркер-поток
    # aiosqlite иначе не даёт процессу завершиться.
    try:
        # Инициализация БД
        await initialize_database()

        logging.info(f"Начинаю пересчет кластеров для годов: {PARSING_YEARS}")

        for year in PARSING_YEARS:
            logging.info(f"--- Обработка {year} года ---")
            await run_clustering(enrollment_year=year)

        logging.info("--- Обновление маппинга кластеров на группы ---")
        await map_clusters_to_groups()

        logging.info("--- Пересчет статистики предметов ---")
        await calculate_subject_stats()

        logging.info("🚀 Пересчет успешно завершен!")
    finally:
        await close_db_connection()

if __name__ == "__main__":
    asyncio.run(recluster_all())
