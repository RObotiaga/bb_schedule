from __future__ import annotations

from datetime import date, datetime
from typing import Iterable

from app.core.database import get_db_connection


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
)


async def record_usage_event(user_id: int, feature: str) -> None:
    """Record one Mini App usage event for an authenticated Telegram user."""
    if feature not in KNOWN_MINIAPP_FEATURES:
        raise ValueError("unknown feature")

    db = await get_db_connection()
    await db.execute(
        """
        INSERT INTO usage_events (user_id, feature, occurred_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        """,
        (user_id, feature),
    )
    await db.commit()


async def get_usage_statistics(known_features: Iterable[str] = KNOWN_MINIAPP_FEATURES) -> dict:
    db = await get_db_connection()
    today = date.today().isoformat()

    async with db.execute("SELECT COUNT(*) FROM users") as cursor:
        total_users = int((await cursor.fetchone())[0])

    async with db.execute(
        """
        SELECT COUNT(DISTINCT user_id)
        FROM usage_events
        WHERE date(occurred_at, 'localtime') = ?
        """,
        (today,),
    ) as cursor:
        active_today = int((await cursor.fetchone())[0])

    async with db.execute(
        """
        SELECT feature, COUNT(*) AS uses, COUNT(DISTINCT user_id) AS unique_users,
               MAX(occurred_at) AS last_used_at
        FROM usage_events
        GROUP BY feature
        ORDER BY uses DESC, feature ASC
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

    features = []
    for feature in known_features:
        features.append(
            by_feature.get(
                feature,
                {"feature": feature, "uses": 0, "unique_users": 0, "last_used_at": None},
            )
        )

    return {
        "generated_at": datetime.now().isoformat(),
        "total_users": total_users,
        "active_today": active_today,
        "features": features,
        "never_used": [item["feature"] for item in features if item["uses"] == 0],
    }
