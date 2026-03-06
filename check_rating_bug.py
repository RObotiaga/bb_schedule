import asyncio
import json
import sqlite3
import os

from app.core.database import DB_PATH

def run_checks():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    
    count = db.execute("SELECT COUNT(*) as c FROM rating_data").fetchone()
    print(f"Total rows in rating_data: {count['c']}")
    
    count_user = db.execute("SELECT COUNT(*) as c FROM users").fetchone()
    print(f"Total rows in users: {count_user['c']}")
    
    user = db.execute("SELECT * FROM users WHERE record_book_number = '20220831'").fetchone()
    if user:
        print(f"Found in users table. user_id={user['user_id']}")
    else:
        print("Not found in users table.")

    # Check mapping
    mappings = db.execute("SELECT cluster_id, group_name FROM cluster_groups WHERE group_name = 'СОТ-412'").fetchall()
    print(f"Clusters mapped to СОТ-412: {[m['cluster_id'] for m in mappings]}")

    print("\n--- 4. Checking teacher_stats for 'Штрапенин' ---")
    stats = db.execute("SELECT subject, group_name, total_students, passed_students, pass_rate FROM teacher_stats WHERE teacher LIKE '%Штрапенин%'").fetchall()
    for s in stats:
        print(f"Stat: subject={s['subject']}, group={s['group_name']}, total={s['total_students']}, passed={s['passed_students']}, rate={s['pass_rate']}")

if __name__ == '__main__':
    run_checks()
