import sqlite3
from pathlib import Path

DB_PATH = Path("aria.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("SELECT id, tool_name, baseline_fitness FROM improvement_history WHERE result = 'deployed'").fetchall()
print(f"Found {len(rows)} deployed records. Resetting baseline_fitness to 0.0 ...")
conn.execute("UPDATE improvement_history SET baseline_fitness = 0.0 WHERE result = 'deployed'")
conn.commit()
print("Done. Rollback cascade is now disabled.")
conn.close()
