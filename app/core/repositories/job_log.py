import json
from datetime import datetime
from typing import List
from app.core.database import get_db_connection

async def save_job_log(job_name: str, start_time: datetime, end_time: datetime, status: str, details: dict):
    db = await get_db_connection()
    await db.execute("""
        INSERT INTO job_logs (job_name, start_time, end_time, status, details_json) 
        VALUES (?, ?, ?, ?, ?)
    """, (
        job_name, 
        start_time.isoformat(), 
        end_time.isoformat(), 
        status, 
        json.dumps(details, ensure_ascii=False)
    ))
    await db.commit()

async def get_last_two_job_logs(job_name: str) -> List[dict]:
    db = await get_db_connection()
    async with db.execute(
        "SELECT start_time, end_time, status, details_json FROM job_logs WHERE job_name = ? ORDER BY start_time DESC LIMIT 2", 
        (job_name,)
    ) as cursor:
        rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append({
                "start_time": datetime.fromisoformat(row["start_time"]),
                "end_time": datetime.fromisoformat(row["end_time"]),
                "status": row["status"],
                "details": json.loads(row["details_json"])
            })
        return result

async def cleanup_old_job_logs(days: int = 30):
    db = await get_db_connection()
    await db.execute("DELETE FROM job_logs WHERE start_time < datetime('now', ?)", (f"-{days} days",))
    await db.commit()
