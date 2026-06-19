CREATE TABLE IF NOT EXISTS token_waste_findings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    waste_type          TEXT NOT NULL,
    description         TEXT NOT NULL,
    estimated_tokens_wasted_per_cycle REAL,
    evidence_json       TEXT NOT NULL,
    severity            TEXT NOT NULL DEFAULT 'low',  -- 'low'|'medium'|'high'
    status              TEXT NOT NULL DEFAULT 'active',
    first_detected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    snapshot_id         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_token_waste_type ON token_waste_findings(waste_type, status);

-- Also ensure cycle_traces exists for tests and future usage if missing.
CREATE TABLE IF NOT EXISTS cycle_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id TEXT,
    timestamp DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tokens_used INTEGER
);
