import sqlite3
import os

db_path = r"c:\Users\Seratul Mustakim\Desktop\My Works\ARIA\aria.db"

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if table exists
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tool_stats'")
if cursor.fetchone():
    # Insert or replace
    cursor.execute("""
        INSERT OR REPLACE INTO tool_stats (tool_name, success_rate, avg_latency, avg_memory_mb, avg_tokens_used, total_executions)
        VALUES ('string_processor_tool', 1.0, 0.5, 10.0, 50, 10)
    """)
    conn.commit()
    print("Seeded baseline stats for string_processor_tool.")
else:
    print("Table tool_stats does not exist yet.")

conn.close()
