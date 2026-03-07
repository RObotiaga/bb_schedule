import asyncio
import logging
from app.core.database import initialize_database
from app.services.subject_stats import calculate_subject_stats

logging.basicConfig(level=logging.INFO)

async def main():
    print("Инициализация таблиц...")
    await initialize_database()
    print("Расчёт статистики по предметам...")
    await calculate_subject_stats()
    print("Готово!")

if __name__ == "__main__":
    asyncio.run(main())
