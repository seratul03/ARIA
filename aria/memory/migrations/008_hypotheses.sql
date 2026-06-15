CREATE TABLE IF NOT EXISTS hypotheses (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type              TEXT NOT NULL,  -- 'cluster' | 'architectural_pattern'
    source_id                INTEGER NOT NULL,
    root_cause_summary       TEXT NOT NULL,   -- "Root cause: Network instability"
    proposed_fix_summary     TEXT NOT NULL,   -- "Possible fix: Add retry with exponential backoff"
    target_tools             TEXT NOT NULL,   -- JSON array — which tool(s) a cycle should target
    confidence               REAL,
    status                   TEXT NOT NULL DEFAULT 'proposed', -- proposed|accepted|rejected|implemented
    created_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_improvement_id  INTEGER,         -- FK -> improvement_history.id, set when implemented
    attempt_count            INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_hypothesis_status ON hypotheses(status);
