CREATE TABLE IF NOT EXISTS failure_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name           TEXT NOT NULL,
    source              TEXT NOT NULL,
    error_type          TEXT,
    error_message       TEXT,
    stack_trace         TEXT,
    traceback_signature TEXT NOT NULL,
    input_snapshot      TEXT,
    cycle_id            INTEGER,
    timestamp           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_failure_tool ON failure_history(tool_name);
CREATE INDEX IF NOT EXISTS idx_failure_signature ON failure_history(traceback_signature);
CREATE INDEX IF NOT EXISTS idx_failure_source ON failure_history(source);
