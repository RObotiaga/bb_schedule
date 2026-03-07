import asyncio
import json
import os
import sys

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.database import get_db_connection, initialize_database
from app.services.db_transfer import export_rating_data, import_rating_data

async def test_db_transfer():
    print("Testing DB Transfer...")
    await initialize_database()
    db = await get_db_connection()

    # 1. Clean up first
    await db.execute("DELETE FROM rating_data")
    await db.execute("DELETE FROM cluster_groups")
    await db.execute("DELETE FROM teacher_stats")
    await db.commit()

    # 2. Add dummy data
    print("Inserting dummy data...")
    test_rating = {
        "record_book": "123456",
        "enrollment_year": 2022,
        "subjects_json": json.dumps([{"subject": "Math", "grade": "5"}]),
        "total_subjects": 1,
        "passed_subjects": 1,
        "pass_rate": 100.0,
        "last_academic_year": "2023/2024"
    }
    await db.execute("""
        INSERT INTO rating_data (record_book, enrollment_year, subjects_json, total_subjects, passed_subjects, pass_rate, last_academic_year)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, tuple(test_rating.values()))

    test_cluster = {"cluster_id": 1, "group_name": "TEST-101", "similarity": 0.95}
    await db.execute("INSERT INTO cluster_groups (cluster_id, group_name, similarity) VALUES (?, ?, ?)", tuple(test_cluster.values()))

    test_teacher = {
        "teacher": "Ivanov I.I.",
        "subject": "Math",
        "group_name": "TEST-101",
        "total_students": 30,
        "passed_students": 28,
        "pass_rate": 93.3,
        "academic_year": "2023/2024"
    }
    await db.execute("""
        INSERT INTO teacher_stats (teacher, subject, group_name, total_students, passed_students, pass_rate, academic_year)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, tuple(test_teacher.values()))
    await db.commit()

    # 3. Export
    print("Exporting data...")
    exported_json = await export_rating_data()
    data = json.loads(exported_json)
    assert len(data["rating_data"]) > 0
    assert len(data["cluster_groups"]) > 0
    assert len(data["teacher_stats"]) > 0
    print(f"Exported {len(data['rating_data'])} items from rating_data")

    # 4. Clear tables
    print("Clearing tables...")
    await db.execute("DELETE FROM rating_data")
    await db.execute("DELETE FROM cluster_groups")
    await db.execute("DELETE FROM teacher_stats")
    await db.commit()

    # 5. Import
    print("Importing data...")
    success = await import_rating_data(exported_json)
    assert success is True

    # 6. Verify
    print("Verifying data...")
    cursor = await db.execute("SELECT * FROM rating_data WHERE record_book = '123456'")
    row = await cursor.fetchone()
    assert row is not None
    assert row["pass_rate"] == 100.0

    cursor = await db.execute("SELECT * FROM cluster_groups WHERE cluster_id = 1")
    row = await cursor.fetchone()
    assert row is not None
    assert row["group_name"] == "TEST-101"

    cursor = await db.execute("SELECT * FROM teacher_stats WHERE teacher = 'Ivanov I.I.'")
    row = await cursor.fetchone()
    assert row is not None
    assert row["pass_rate"] == 93.3

    print("✅ All tests passed!")

if __name__ == "__main__":
    asyncio.run(test_db_transfer())
