import asyncio

import pytest


@pytest.fixture()
def isolated_db(tmp_path, monkeypatch):
    from app.core import database

    asyncio.run(database.close_db_connection())
    db_path = tmp_path / "schedule.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    asyncio.run(database.initialize_database())
    yield database
    asyncio.run(database.close_db_connection())


@pytest.mark.asyncio
async def test_schedule_archive_returns_old_and_new_dates(isolated_db):
    from app.core.database import get_db_connection
    from app.web.app import _schedule_for_group

    db = await get_db_connection()
    rows = [
        ("Ф", "5", "СОт-512", "нечетная", "2025-02-10", "08:30 - 10:05", "Старая дисциплина", "Иванов И.И.", "101"),
        ("Ф", "5", "СОт-512", "четная", "2026-09-01", "14:30 - 16:05", "Текущая дисциплина", "Петров П.П.", "202"),
        ("Ф", "5", "СОт-512", "нечетная", "2026-12-15", "16:15 - 17:50", "Будущая дисциплина", "Сидоров С.С.", "303"),
    ]
    await db.executemany(
        """
        INSERT INTO schedule
            (faculty, course, group_name, week_type, lesson_date, time, subject, teacher, location)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    await db.commit()

    days = await _schedule_for_group("СОт-512")

    assert [day["date"] for day in days] == ["2025-02-10", "2026-09-01", "2026-12-15"]
    assert [day["lessons"][0]["subject"] for day in days] == [
        "Старая дисциплина",
        "Текущая дисциплина",
        "Будущая дисциплина",
    ]


@pytest.mark.asyncio
async def test_usage_statistics_counts_users_active_today_and_never_used(isolated_db):
    from app.core.database import get_db_connection
    from app.core.repositories.analytics import get_usage_statistics, record_usage_event

    db = await get_db_connection()
    await db.executemany(
        "INSERT INTO users (user_id, group_name) VALUES (?, ?)",
        [(100, "СОт-512"), (200, "СОт-512"), (300, None)],
    )
    await db.commit()

    await record_usage_event(100, "miniapp_open")
    await record_usage_event(100, "schedule_view")
    await record_usage_event(100, "schedule_view")

    stats = await get_usage_statistics()
    features = {item["feature"]: item for item in stats["features"]}

    assert stats["total_users"] == 3
    assert stats["active_today"] == 1
    assert features["schedule_view"]["uses"] == 2
    assert features["schedule_view"]["unique_users"] == 1
    assert "teacher_search" in stats["never_used"]
    assert "schedule_view" not in stats["never_used"]


@pytest.mark.asyncio
async def test_usage_event_rejects_unknown_feature(isolated_db):
    from app.core.repositories.analytics import record_usage_event

    with pytest.raises(ValueError, match="unknown feature"):
        await record_usage_event(100, "made_up_feature")


def test_miniapp_enhancement_has_archive_navigation_without_generated_forecast():
    from pathlib import Path

    script = Path("app/web/miniapp_enhancements.js").read_text(encoding="utf-8")

    assert "/api/schedule?group=" in script
    assert "Доступные даты в базе" in script
    assert "archiveDateSelect" in script
    assert "chooseNearestAvailableDate" in script
    # The archive is built from data.days returned by the DB-backed endpoint,
    # not by generating a fixed number of future calendar days.
    assert "archive.days = (data.days || [])" in script
