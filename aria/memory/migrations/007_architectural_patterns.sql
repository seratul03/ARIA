-- Migration 007: architectural_patterns schema

CREATE TABLE IF NOT EXISTS architectural_patterns (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    cluster_id      INTEGER NOT NULL,    -- FK -> root_cause_clusters.id
    pattern_name    TEXT NOT NULL,       -- e.g. "Missing Retry Logic for Network Calls"
    description     TEXT NOT NULL,       -- 1-2 sentence LLM-generated explanation
    affected_tools  TEXT NOT NULL,       -- JSON array, copied from cluster.tool_names at creation
    evidence_count  INTEGER NOT NULL,    -- = cluster.total_occurrences at creation
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'resolved'
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_arch_pattern_status ON architectural_patterns(status);
