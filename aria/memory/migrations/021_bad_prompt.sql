CREATE TABLE IF NOT EXISTS bad_prompt_findings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_type         TEXT NOT NULL,   -- 'generation' | 'classification' | 'hypothesis' | 'refinement'
    finding_type        TEXT NOT NULL,
    description         TEXT NOT NULL,
    evidence_json       TEXT NOT NULL,
    correlation_metric  REAL,            -- how strongly this is correlated with bad outcomes
    status              TEXT NOT NULL DEFAULT 'active',
    first_detected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    snapshot_id         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bad_prompt_type ON bad_prompt_findings(finding_type, status);
