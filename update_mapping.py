import asyncio
import logging
from app.core.database import initialize_database, get_db_connection
from app.services.cluster_mapper import map_clusters_to_groups
from app.services.subject_stats import calculate_subject_stats

logging.basicConfig(level=logging.INFO)

async def main():
    await initialize_database()
    await map_clusters_to_groups()
    await calculate_subject_stats()
    
    db = await get_db_connection()
    c = await db.execute('SELECT COUNT(DISTINCT group_name) FROM cluster_groups')
    row = await c.fetchone()
    print("Unique Mapped groups:", row[0] if row else 0)

    c = await db.execute("SELECT group_name, COUNT(*) FROM cluster_groups GROUP BY group_name ORDER BY COUNT(*) DESC LIMIT 10")
    print("Top groups by cluster count:", await c.fetchall())

if __name__ == "__main__":
    asyncio.run(main())
