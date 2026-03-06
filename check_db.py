import asyncio
from app.core.database import get_db_connection

async def main():
    db = await get_db_connection()
    async with db.execute("SELECT name FROM sqlite_master WHERE type='table'") as c:
        rows = await c.fetchall()
        print("Tables:", [r[0] for r in rows])

if __name__ == "__main__":
    asyncio.run(main())
