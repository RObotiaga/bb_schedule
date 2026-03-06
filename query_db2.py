import asyncio
from app.core.database import get_db_connection

async def main():
    db = await get_db_connection()
    async with db.execute("""
        SELECT subject, teacher, SUM(total_students), SUM(passed_students),
        ROUND(CAST(SUM(passed_students) AS FLOAT) / SUM(total_students) * 100, 1) as rate
        FROM teacher_stats
        GROUP BY subject, teacher
        HAVING SUM(total_students) > 0
        ORDER BY subject, rate DESC
        LIMIT 20
    """) as cursor:
        rows = await cursor.fetchall()
        for r in rows:
            print(dict(r))

if __name__ == "__main__":
    asyncio.run(main())
