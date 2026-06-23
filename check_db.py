import sqlite3
conn = sqlite3.connect('data/aria.db')
print("Deployments:")
for row in conn.execute("SELECT tool_name, timestamp FROM improvement_history WHERE result='deployed' ORDER BY timestamp DESC"):
    print(row)
print("\nMax Executions:")
for row in conn.execute("SELECT tool_name, MAX(timestamp) FROM tool_executions GROUP BY tool_name"):
    print(row)
