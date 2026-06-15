CREATE TABLE IF NOT EXISTS rule_applications (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id                  INTEGER NOT NULL,   -- FK -> engineering_rules.id
    cycle_id                 TEXT,               -- FK -> cycle_traces.cycle_id
    improvement_history_id   INTEGER,            -- FK -> improvement_history.id, set when cycle concludes
    applied_at               DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    outcome                  TEXT NOT NULL DEFAULT 'pending'  -- 'pending' | 'success' | 'failure'
);

CREATE INDEX IF NOT EXISTS idx_rule_app_rule ON rule_applications(rule_id);
CREATE INDEX IF NOT EXISTS idx_rule_app_outcome ON rule_applications(outcome);

ALTER TABLE review_queue ADD COLUMN cycle_id TEXT;
