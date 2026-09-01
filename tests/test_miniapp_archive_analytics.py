import asyncio
import os

import pytest

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456789:AABBCcDDEEFFGG")
os.environ.setdefault("ADMIN_ID", "42")


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
async def test_schedule_range_and_exact_date_use_only_database_rows(isolated_db):
    from app.core.database import get_db_connection
    from app.web.entrypoint import api_schedule_by_date, api_schedule_range

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

    date_range = await api_schedule_range("СОт-512")
    assert date_range == {
        "group": "СОт-512",
        "min_date": "2025-02-10",
        "max_date": "2026-12-15",
    }

    current = await api_schedule_by_date("СОт-512", "2026-09-01", False, None)
    assert len(current["days"]) == 1
    assert current["days"][0]["lessons"][0]["subject"] == "Текущая дисциплина"

    # A calendar day may be shown in the old-style strip, but no lesson is
    # synthesized for it: an empty DB day stays empty.
    missing = await api_schedule_by_date("СОт-512", "2026-09-02", False, None)
    assert missing["days"] == []

    future = await api_schedule_by_date("СОт-512", "2026-12-15", False, None)
    assert future["days"][0]["lessons"][0]["subject"] == "Будущая дисциплина"


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


def test_miniapp_entrypoint_exposes_schedule_and_analytics_routes():
    from app.web.entrypoint import app

    paths = {route.path for route in app.routes}
    assert "/api/schedule/range" in paths
    assert "/api/schedule/by-date" in paths
    assert "/api/analytics/event" in paths
    assert "/api/admin/analytics" in paths
    assert "/miniapp-enhancements.js" in paths


def test_date_strip_keeps_old_style_and_loads_both_directions_dynamically():
    from pathlib import Path

    script = Path("app/web/miniapp_enhancements.js").read_text(encoding="utf-8")

    # Keep the existing compact weekday + day-number strip instead of a
    # separate archive selector/page.
    assert "day-chip" in script
    assert "dayStrip" in script
    assert "data-db-date" in script
    assert "archiveControls" not in script
    assert "archiveDateSelect" not in script

    # Only date bounds are loaded first. The strip moves a bounded window and
    # exact lesson rows are fetched only when the user selects a date.
    assert "/api/schedule/range" in script
    assert "/api/schedule/by-date" in script
    assert "STRIP_LOAD_CHUNK_DAYS" in script
    assert "MAX_STRIP_DAYS = 42" in script
    assert 'shiftStripWindow("before")' in script
    assert 'shiftStripWindow("after")' in script

    # Calendar navigation may include empty days, but the schedule itself is
    # never extrapolated or predicted client-side.
    assert "predict" not in script.lower()
    assert "forecast" not in script.lower()
