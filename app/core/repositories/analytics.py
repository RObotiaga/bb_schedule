from __future__ import annotations

from datetime import datetime
from typing import Iterable
from zoneinfo import ZoneInfo

from app.core.database import get_db_connection


LOCAL_TZ = ZoneInfo("Asia/Yekaterinburg")

KNOWN_MINIAPP_FEATURES = (
    "miniapp_open",
    "schedule_view",
    "schedule_date_change",
    "schedule_group_change",
    "teacher_search",
    "teacher_schedule_view",
    "teacher_subscription_change",
    "session_view",
    "session_refresh",
    "session_settings_change",
    "subject_note_open",
    "subject_note_save",
    "subject_checklist_change",
    "rating_view",
    "subject_stats_search",
    "subject_stats_view",
    "admin_status_view",
    "admin_job_start",
    "admin_analytics_view",
)


async def _ensure_usage_table() -> None:
    db = await get_db_connection()
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS usage_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            feature TEXT NOT NULL,
            event_date TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )
        """
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_events_date_user "
        "ON usage_events (event_date, user_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_usage_events_feature "
        "ON usage_events (feature)"
    )
    await db.commit()


async def record_usage_event(user_id: int, feature: str) -> None:
    """Record one successful Mini App feature use for an authenticated user."""
    if feature not in KNOWN_MINIAPP_FEATURES:
        raise ValueError("unknown feature")

    await _ensure_usage_table()
    db = await get_db_connection()
    now = datetime.now(LOCAL_TZ)
    await db.execute(
        """
        INSERT INTO usage_events (user_id, feature, event_date, occurred_at)
        VALUES (?, ?, ?, ?)
        """,
        (user_id, feature, now.date().isoformat(), now.isoformat()),
    )
    await db.commit()


async def get_usage_statistics(known_features: Iterable[str] = KNOWN_MINIAPP_FEATURES) -> dict:
    await _ensure_usage_table()
    db = await get_db_connection()
    today = datetime.now(LOCAL_TZ).date().isoformat()

    async with db.execute("SELECT COUNT(*) FROM users") as cursor:
        total_users = int((await cursor.fetchone())[0])

    async with db.execute(
        "SELECT COUNT(DISTINCT user_id) FROM usage_events WHERE event_date = ?",
        (today,),
    ) as cursor:
        active_today = int((await cursor.fetchone())[0])

    async with db.execute(
        """
        SELECT feature,
               COUNT(*) AS uses,
               COUNT(DISTINCT user_id) AS unique_users,
               MAX(occurred_at) AS last_used_at
        FROM usage_events
        GROUP BY feature
        """
    ) as cursor:
        rows = await cursor.fetchall()

    by_feature = {
        row["feature"]: {
            "feature": row["feature"],
            "uses": int(row["uses"]),
            "unique_users": int(row["unique_users"]),
            "last_used_at": row["last_used_at"],
        }
        for row in rows
    }

    features = [
        by_feature.get(
            feature,
            {"feature": feature, "uses": 0, "unique_users": 0, "last_used_at": None},
        )
        for feature in known_features
    ]
    features.sort(key=lambda item: (-item["uses"], item["feature"]))

    return {
        "generated_at": datetime.now(LOCAL_TZ).isoformat(),
        "timezone": "Asia/Yekaterinburg",
        "total_users": total_users,
        "active_today": active_today,
        "features": features,
        "never_used": [item["feature"] for item in features if item["uses"] == 0],
    }
