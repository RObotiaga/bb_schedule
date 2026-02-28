import asyncio
import logging
import sys
import os

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__))))

from app.core.logger import setup_logging
from app.core.database import initialize_database
from app.services.schedule_sync import run_full_sync

async def main():
    setup_logging()
    logging.info("🏃 Запуск ручного парсинга и синхронизации расписания...")
    
    # Инициализация базы данных (создание таблиц, если их нет)
    await initialize_database()
    
    # Запуск полной синхронизации (загрузка, парсинг, сохранение в БД)
    success = await run_full_sync()
    
    if success:
        logging.info("✅ Синхронизация расписания успешно завершена.")
        sys.exit(0)
    else:
        logging.error("❌ Возникли ошибки при синхронизации расписания.")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
