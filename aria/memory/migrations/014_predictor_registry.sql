CREATE TABLE IF NOT EXISTS predictor_registry (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    predictor_type      TEXT NOT NULL,  -- 'success' | 'root_cause' | 'risk'
    version             INTEGER NOT NULL,
    model_path          TEXT NOT NULL,  -- relative path under aria/predictors/models/
    feature_schema_hash TEXT NOT NULL,  -- hash of the feature list used at training time
    train_samples       INTEGER NOT NULL,
    test_samples        INTEGER NOT NULL,
    test_accuracy       REAL NOT NULL,
    test_auc            REAL,           -- for binary classifiers
    status              TEXT NOT NULL DEFAULT 'candidate',  -- candidate|active|retired
    trained_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activated_at        DATETIME,
    retired_at          DATETIME,
    notes               TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_predictor_active
    ON predictor_registry(predictor_type, status)
    WHERE status = 'active';   -- at most one active predictor per type at any time

CREATE INDEX IF NOT EXISTS idx_predictor_type ON predictor_registry(predictor_type, version);
