import json
import logging
from typing import Dict, Any
from app.core.database import get_db_connection

async def export_rating_data() -> str:
    """
    Exports rating_data, cluster_groups, and teacher_stats tables to a JSON string.
    """
    db = await get_db_connection()
    export_data = {}

    # 1. rating_data
    cursor = await db.execute("SELECT * FROM rating_data")
    rows = await cursor.fetchall()
    export_data["rating_data"] = [dict(row) for row in rows]

    # 2. cluster_groups
    cursor = await db.execute("SELECT * FROM cluster_groups")
    rows = await cursor.fetchall()
    export_data["cluster_groups"] = [dict(row) for row in rows]

    # 3. teacher_stats
    cursor = await db.execute("SELECT * FROM teacher_stats")
    rows = await cursor.fetchall()
    export_data["teacher_stats"] = [dict(row) for row in rows]

    return json.dumps(export_data, ensure_ascii=False, indent=2)

async def import_rating_data(json_data: str) -> bool:
    """
    Imports rating data from a JSON string into the database.
    Uses REPLACE for all tables to update existing records.
    """
    try:
        data = json.loads(json_data)
        db = await get_db_connection()

        # Import rating_data
        if "rating_data" in data:
            for item in data["rating_data"]:
                columns = ", ".join(item.keys())
                placeholders = ", ".join(["?"] * len(item))
                values = tuple(item.values())
                await db.execute(f"INSERT OR REPLACE INTO rating_data ({columns}) VALUES ({placeholders})", values)

        # Import cluster_groups
        if "cluster_groups" in data:
            for item in data["cluster_groups"]:
                columns = ", ".join(item.keys())
                placeholders = ", ".join(["?"] * len(item))
                values = tuple(item.values())
                await db.execute(f"INSERT OR REPLACE INTO cluster_groups ({columns}) VALUES ({placeholders})", values)

        # Import teacher_stats
        if "teacher_stats" in data:
            for item in data["teacher_stats"]:
                columns = ", ".join(item.keys())
                placeholders = ", ".join(["?"] * len(item))
                values = tuple(item.values())
                await db.execute(f"INSERT OR REPLACE INTO teacher_stats ({columns}) VALUES ({placeholders})", values)

        await db.commit()
        return True
    except Exception as e:
        logging.exception("Error during rating data import")
        return False
