-- Migration 006: root_cause_clusters schema

CREATE TABLE IF NOT EXISTS root_cause_clusters (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    root_cause_category  TEXT NOT NULL,
    cluster_label        TEXT,                 -- short human label, e.g. "network timeout handling"
    pattern_ids          TEXT NOT NULL,        -- JSON array of failure_patterns.id
    tool_names           TEXT NOT NULL,        -- JSON array, union across member patterns
    total_occurrences    INTEGER NOT NULL,
    similarity_threshold REAL NOT NULL,
    created_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cluster_category ON root_cause_clusters(root_cause_category);
