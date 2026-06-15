ALTER TABLE failure_patterns ADD COLUMN root_cause_category TEXT;
ALTER TABLE failure_patterns ADD COLUMN root_cause_confidence REAL;
ALTER TABLE failure_patterns ADD COLUMN root_cause_method TEXT;        -- 'heuristic' | 'llm' | 'manual'
ALTER TABLE failure_patterns ADD COLUMN root_cause_assigned_at DATETIME;

CREATE INDEX IF NOT EXISTS idx_pattern_root_cause ON failure_patterns(root_cause_category);
CREATE INDEX IF NOT EXISTS idx_improvement_weakness ON improvement_history(weakness_category);
