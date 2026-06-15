CREATE TABLE IF NOT EXISTS engineering_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_text           TEXT NOT NULL,             -- "Always validate external input before use"
    category            TEXT NOT NULL,             -- one of Phase 2's RootCauseCategory values
    scope               TEXT,                       -- e.g. "outbound HTTP calls" — NULL until refined (Day 22-24)
    source_type         TEXT NOT NULL,              -- 'architectural_pattern' | 'hypothesis' | 'cluster' | 'refinement'
    source_id           INTEGER NOT NULL,            -- FK into the relevant Phase 2 table, or engineering_rules.id for refinements
    initial_confidence  REAL NOT NULL,               -- immutable, set once at creation
    confidence          REAL NOT NULL DEFAULT 0.5,
    status              TEXT NOT NULL DEFAULT 'candidate',  -- candidate|active|deprecated|merged|superseded
    deprecation_reason  TEXT,
    applications_count  INTEGER NOT NULL DEFAULT 0,
    success_count       INTEGER NOT NULL DEFAULT 0,
    superseded_by       INTEGER,                     -- FK -> engineering_rules.id
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rules_category ON engineering_rules(category);
CREATE INDEX IF NOT EXISTS idx_rules_status ON engineering_rules(status);

-- Table to store version for export
CREATE TABLE IF NOT EXISTS knowledge_export_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    version INTEGER NOT NULL DEFAULT 1
);
INSERT OR IGNORE INTO knowledge_export_state (id, version) VALUES (1, 1);
