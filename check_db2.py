import sqlite3
import os
import sys
if sys.stdout.encoding.lower() != 'utf-8':
    try: sys.stdout.reconfigure(encoding='utf-8')
    except: pass
from aria.config import settings

print("DB Path:", settings.db_path)
conn = sqlite3.connect(settings.db_path)
conn.row_factory = sqlite3.Row

print("Deployments for search_tool:")
for row in conn.execute("SELECT * FROM improvement_history WHERE tool_name='search_tool' AND result='deployed' ORDER BY timestamp DESC"):
    print(dict(row))

print("\nExecutions for search_tool:")
for row in conn.execute("SELECT * FROM tool_executions WHERE tool_name='search_tool' ORDER BY timestamp DESC LIMIT 5"):
    print(dict(row))
