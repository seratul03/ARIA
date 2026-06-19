CREATE TABLE IF NOT EXISTS self_improvement_proposals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    title               TEXT NOT NULL,
    source_finding_type TEXT NOT NULL,  -- 'weakness'|'mistake'|'ineffective'|'token_waste'|'bad_prompt'
    source_finding_id   INTEGER NOT NULL,  -- FK into the relevant finding table
    proposal_text       TEXT NOT NULL,     -- what to change, how, and why
    target_module       TEXT,              -- 'aria/improvement/prompt_builder.py' etc.
    change_type         TEXT NOT NULL,     -- 'prompt_change'|'parameter_change'|'pipeline_change'|'knowledge_change'|'schedule_change'
    success_metric      TEXT NOT NULL,     -- MUST be specific and measurable
    measurement_window_cycles INTEGER NOT NULL DEFAULT 20,  -- how many cycles to evaluate
    priority            TEXT NOT NULL DEFAULT 'medium',  -- 'low'|'medium'|'high'|'critical'
    status              TEXT NOT NULL DEFAULT 'proposed',  -- proposed|accepted|in_progress|implemented|failed|deferred
    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    accepted_at         DATETIME,
    implemented_at      DATETIME,
    implementation_notes TEXT,
    evaluation_at       DATETIME,    -- when to check success_metric
    outcome             TEXT,        -- 'success'|'failure'|'inconclusive' (set at evaluation_at)
    outcome_notes       TEXT,
    snapshot_id         INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_proposal_status ON self_improvement_proposals(status);
CREATE INDEX IF NOT EXISTS idx_proposal_priority ON self_improvement_proposals(priority);
