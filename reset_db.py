import sqlite3

def reset():
    conn = sqlite3.connect('aria.db')
    conn.execute("DELETE FROM tool_executions WHERE tool_name = 'code_executor_tool'")
    conn.commit()
    conn.close()
    print("Database reset for code_executor_tool!")

if __name__ == "__main__":
    reset()
