import sqlite3
import os

db_path = os.path.join("data", "schedule.db")
print(f"Checking DB at: {os.path.abspath(db_path)}")
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cur.fetchall()
print("Tables:", tables)

cur.execute("PRAGMA table_info(job_logs)")
try:
    cols = cur.fetchall()
    print("Columns in job_logs:", cols)
except Exception as e:
    print("Error getting cols:", e)

cur.execute("SELECT * FROM job_logs LIMIT 5")
try:
    rows = cur.fetchall()
    print("job_logs content:", rows)
except Exception as e:
    print("Error getting rows:", e)
conn.close()
