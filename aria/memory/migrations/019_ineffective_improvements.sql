CREATE TABLE IF NOT EXISTS ineffective_improvements (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ineffectiveness_type TEXT NOT NULL,
    scope               TEXT NOT NULL,   -- 'tool:X' | 'strategy:Y' | 'rule:Z' | 'phase:W'
    metric_name         TEXT NOT NULL,
    metric_value        REAL NOT NULL,
    metric_baseline     REAL NOT NULL,
    evidence_json       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'active',
    first_detected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    snapshot_id         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ineffective_type ON ineffective_improvements(ineffectiveness_type, scope);
