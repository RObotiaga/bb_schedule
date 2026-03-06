import asyncio
import sqlite3
import pandas as pd

def main():
    conn = sqlite3.connect('app/core/database.db')
    df = pd.read_sql_query("SELECT subject, teacher, pass_rate, passed_students, total_students FROM teacher_stats", conn)
    print(f"Total rows: {len(df)}")
    print(f"Unique subjects: {df['subject'].nunique()}")
    print("Top 5 subjects by number of teachers:")
    print(df.groupby('subject')['teacher'].nunique().sort_values(ascending=False).head(5))
    conn.close()

if __name__ == "__main__":
    main()
