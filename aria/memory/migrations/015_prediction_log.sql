CREATE TABLE IF NOT EXISTS prediction_log (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    predictor_id            INTEGER NOT NULL,   -- FK -> predictor_registry.id
    evolution_run_id        INTEGER,            -- FK -> evolution_runs.id
    candidate_id            INTEGER,            -- FK -> evolution_candidates.id (if applicable)
    tool_name               TEXT,
    prediction_type         TEXT NOT NULL,      -- 'success' | 'root_cause' | 'risk'
    predicted_value         TEXT NOT NULL,      -- serialized: float for success/risk, category string for root_cause
    predicted_confidence    REAL,
    actual_value            TEXT,               -- filled in after cycle concludes (Day 50)
    feature_vector_hash     TEXT,               -- sha256 of the feature vector used (for audit)
    predicted_at            DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_pred_log_type ON prediction_log(prediction_type);
CREATE INDEX IF NOT EXISTS idx_pred_log_predictor ON prediction_log(predictor_id);
