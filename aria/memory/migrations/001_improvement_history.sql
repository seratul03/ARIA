-- Migration 001: improvement_history schema

CREATE TABLE IF NOT EXISTS improvement_history (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id            INTEGER,              -- FK -> cycle_traces.id (nullable for meta/synthesis cycles that predate linkage)
    improvement_type    TEXT NOT NULL,        -- 'tool' | 'meta' | 'synthesis'
    tool_name           TEXT,                 -- NULL for pure meta/architecture changes
    component_name      TEXT,                 -- e.g. 'aria/core/scheduler.py' for meta cycles
    problem_description TEXT NOT NULL,        -- what weakness/triggering issue this addressed
    triggering_failure_id INTEGER,            -- FK -> failure_history.id (added Day 2; nullable now)
    weakness_category   TEXT,                 -- NULL in Phase 1, populated in Phase 2
    fix_summary         TEXT NOT NULL,        -- human/LLM-readable 1-3 sentence summary
    fix_code_hash       TEXT,                 -- sha256 of the deployed candidate source
    test_suite_hash     TEXT,                 -- hash of the adversarial test suite used
    baseline_fitness    REAL,
    candidate_fitness   REAL,
    fitness_delta       REAL,
    result              TEXT NOT NULL,        -- 'deployed' | 'rejected' | 'rolled_back' | 'pending_review'
    rejection_reason    TEXT,
    git_commit_hash     TEXT,
    timestamp           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_improvement_tool ON improvement_history(tool_name);
CREATE INDEX IF NOT EXISTS idx_improvement_result ON improvement_history(result);
CREATE INDEX IF NOT EXISTS idx_improvement_timestamp ON improvement_history(timestamp);
