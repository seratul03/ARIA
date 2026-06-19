CREATE TABLE IF NOT EXISTS architectural_weaknesses (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    weakness_type       TEXT NOT NULL,   -- see WEAKNESS_TYPES below
    title               TEXT NOT NULL,   -- short human label, e.g. "Network tools lack retry logic"
    evidence_json       TEXT NOT NULL,   -- JSON: {source_type, source_ids, metrics}
    severity            TEXT NOT NULL DEFAULT 'medium',  -- 'low'|'medium'|'high'|'critical'
    status              TEXT NOT NULL DEFAULT 'active',  -- 'active'|'addressed'|'accepted_risk'
    first_detected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    addressed_by_proposal_id INTEGER,   -- FK -> self_improvement_proposals.id
    snapshot_id         INTEGER NOT NULL -- FK -> self_model_snapshots.id
);

CREATE INDEX IF NOT EXISTS idx_weakness_type ON architectural_weaknesses(weakness_type);
CREATE INDEX IF NOT EXISTS idx_weakness_status ON architectural_weaknesses(status);
