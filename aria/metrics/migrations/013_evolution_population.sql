CREATE TABLE IF NOT EXISTS evolution_population (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tool_name           TEXT NOT NULL,
    root_cause_category TEXT,                    -- Phase 2 category for this candidate's fix
    candidate_id        INTEGER NOT NULL,         -- FK -> evolution_candidates.id
    strategy            TEXT NOT NULL,
    fix_summary         TEXT NOT NULL,
    composite_score     REAL NOT NULL,
    deployed            INTEGER NOT NULL DEFAULT 0,  -- 1 if this candidate was actually deployed
    deployment_durable  INTEGER,                  -- NULL until determined; 1=held, 0=rolled_back
    fitness_delta       REAL,
    added_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pop_tool ON evolution_population(tool_name);
CREATE INDEX IF NOT EXISTS idx_pop_score ON evolution_population(composite_score DESC);
