-- Migration 004: failure patterns schema

CREATE TABLE IF NOT EXISTS failure_patterns (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    traceback_signature     TEXT NOT NULL UNIQUE,
    representative_failure_id INTEGER NOT NULL,  -- FK -> failure_history.id (earliest instance)
    tool_names              TEXT NOT NULL,        -- JSON array, e.g. ["search_tool","weather_tool"]
    occurrence_count        INTEGER NOT NULL DEFAULT 1,
    first_seen              DATETIME NOT NULL,
    last_seen               DATETIME NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'active', -- 'active' | 'resolved'
    resolved_by_improvement_id INTEGER           -- FK -> improvement_history.id, nullable
);

CREATE INDEX IF NOT EXISTS idx_pattern_signature ON failure_patterns(traceback_signature);
CREATE INDEX IF NOT EXISTS idx_pattern_status ON failure_patterns(status);
