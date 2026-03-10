import pytest
import aiosqlite
from datetime import datetime, timezone
import json
from app.core.repositories.job_log import save_job_log, get_last_two_job_logs, cleanup_old_job_logs
from app.core import database

@pytest.fixture(autouse=True)
async def patch_db_path(test_db, monkeypatch):
    """Автоматически подменяет DB_PATH на тестовую БД для всех тестов."""
    monkeypatch.setattr(database, "DB_PATH", test_db)
    yield
    await database.close_db_connection()

@pytest.mark.asyncio
async def test_job_logs_lifecycle(test_db):
    time_start1 = datetime.now(timezone.utc)
    time_end1 = datetime.now(timezone.utc)
    
    import asyncio
    await asyncio.sleep(0.1) # небольшая задержка для разных timestamp
    
    time_start2 = datetime.now(timezone.utc)
    time_end2 = datetime.now(timezone.utc)
    
    details = {"downloaded": 10}
    
    # Сохраняем два лога
    await save_job_log("test_job", time_start1, time_end1, "SUCCESS", details)
    await save_job_log("test_job", time_start2, time_end2, "ERROR", {"error": "test"})
    await save_job_log("other_job", time_start2, time_end2, "SUCCESS", {})
    
    # Получаем логи для "test_job"
    logs = await get_last_two_job_logs("test_job")
    
    assert len(logs) == 2
    assert logs[0]["status"] == "ERROR"
    assert logs[1]["status"] == "SUCCESS"
    assert logs[0]["details"]["error"] == "test"
    assert logs[1]["details"]["downloaded"] == 10
    
    # Очистка (по умолчанию 30 дней, так что ничего не удалится)
    await cleanup_old_job_logs(days=30)
    logs_after = await get_last_two_job_logs("test_job")
    assert len(logs_after) == 2
