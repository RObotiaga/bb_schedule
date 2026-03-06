import asyncio
import sys
from app.core.database import get_db_connection

async def main():
    db = await get_db_connection()
    async with db.execute("SELECT job_name, start_time, end_time, status FROM job_logs ORDER BY start_time DESC LIMIT 10") as c:
        rows = await c.fetchall()
        print("--- RECENT JOB LOGS ---")
        for r in rows:
            print(dict(r))

if __name__ == "__main__":
    asyncio.run(main())    
