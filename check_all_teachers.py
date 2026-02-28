import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), "data", "schedule.db")

if not os.path.exists(db_path):
    print("DB not found!")
else:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    # Получим список всех преподавателей, чтобы посмотреть формат
    cur.execute("SELECT DISTINCT teacher FROM schedule WHERE teacher != '' AND teacher IS NOT NULL LIMIT 50")
    results = cur.fetchall()
    print("Некоторые преподаватели из БД:")
    for r in results:
        print(f"'{r[0]}'")
        
    # И поищем по 'Евгений Алексеевич' на всякий случай
    cur.execute("SELECT DISTINCT teacher FROM schedule WHERE teacher LIKE '%Евгений Алексеевич%' OR teacher LIKE '%Е. А.%' OR teacher LIKE '%Е.А.%'")
    results = cur.fetchall()
    print("Преподаватели с инициалами Е.А.:")
    for r in results:
        print(f"'{r[0]}'")
    conn.close()
