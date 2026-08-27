"""Ручная синхронизация расписания: скачивание Excel с Blackboard и запись в БД.

Запуск (без старта бота):
    python -m tools.sync_manual
или внутри контейнера:
    docker-compose run --rm schedule_bot python /app/tools/sync_manual.py
"""
import asyncio
import sys
from pathlib import Path

# Корень проекта в sys.path при запуске как скрипта
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.logger import setup_logging
from app.core.database import close_db_connection
from app.services.schedule_sync import run_full_sync


async def main() -> int:
    setup_logging()
    try:
        # run_full_sync() сам гарантирует initialize_database().
        ok = await run_full_sync()
        print("Синхронизация:", "успешно завершена" if ok else "завершилась с ошибкой")
        return 0 if ok else 1
    finally:
        # Без закрытия не-daemon воркер-поток aiosqlite не даст процессу завершиться.
        await close_db_connection()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
