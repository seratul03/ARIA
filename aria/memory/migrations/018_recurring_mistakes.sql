CREATE TABLE IF NOT EXISTS recurring_mistakes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    mistake_type        TEXT NOT NULL,
    description         TEXT NOT NULL,
    evidence_json       TEXT NOT NULL,  -- specific cycle_ids, candidate_ids, counts
    occurrence_count    INTEGER NOT NULL DEFAULT 1,
    first_seen_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    status              TEXT NOT NULL DEFAULT 'active',  -- 'active'|'resolved'|'accepted'
    snapshot_id         INTEGER NOT NULL -- FK -> self_model_snapshots.id
);

CREATE INDEX IF NOT EXISTS idx_mistake_type ON recurring_mistakes(mistake_type);
CREATE INDEX IF NOT EXISTS idx_mistake_status ON recurring_mistakes(status);
