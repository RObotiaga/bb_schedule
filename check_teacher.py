import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "data", "schedule.db")

if not os.path.exists(db_path):
    print("DB not found!")
else:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT teacher FROM schedule WHERE teacher LIKE '%Сергеев%'")
    results = cur.fetchall()
    print("Teachers found with 'Сергеев':")
    for r in results:
        print(f"'{r[0]}'")
    conn.close()
