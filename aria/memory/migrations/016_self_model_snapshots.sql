CREATE TABLE IF NOT EXISTS self_model_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cycle_number    INTEGER NOT NULL,     -- which meta-introspection cycle this was built from
    schema_version  INTEGER NOT NULL,     -- version of self_model schema; increment on migrations

    -- Aggregates sourced from Phase 1:
    total_improvement_cycles        INTEGER,
    deployed_improvements           INTEGER,
    rolled_back_improvements        INTEGER,
    overall_deploy_rate             REAL,
    active_failure_patterns         INTEGER,
    resolved_failure_patterns       INTEGER,

    -- Aggregates sourced from Phase 2:
    root_cause_breakdown_json       TEXT,   -- JSON {category: fraction} (by_occurrence)
    active_architectural_patterns   INTEGER,
    resolved_architectural_patterns INTEGER,
    open_hypotheses                 INTEGER,
    implemented_hypotheses          INTEGER,

    -- Aggregates sourced from Phase 3:
    active_rules                    INTEGER,
    candidate_rules                 INTEGER,
    deprecated_rules                INTEGER,
    top_rule_confidence             REAL,
    refinement_chains_active        INTEGER,

    -- Aggregates sourced from Phase 4:
    total_evolution_runs            INTEGER,
    strategy_win_rates_json         TEXT,   -- JSON {strategy: win_rate}
    mutation_win_rates_json         TEXT,   -- JSON {operator: win_rate}
    avg_candidates_per_run          REAL,
    population_sizes_json           TEXT,   -- JSON {tool_name: population_size}

    -- Aggregates sourced from Phase 5:
    predictor_summary_json          TEXT,   -- JSON {type: {version, status, auc, accuracy}}
    active_predictor_count          INTEGER,

    -- Phase 6 (populated by Days 52-60):
    active_weaknesses               INTEGER DEFAULT 0,
    resolved_weaknesses             INTEGER DEFAULT 0,
    recurring_mistake_count         INTEGER DEFAULT 0,
    open_proposals                  INTEGER DEFAULT 0,
    implemented_proposals           INTEGER DEFAULT 0,
    failed_proposals                INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_self_model_cycle ON self_model_snapshots(cycle_number);

-- Triggers to enforce append-only nature (only Phase 6 columns can be updated)
CREATE TRIGGER IF NOT EXISTS prevent_self_model_snapshots_update
BEFORE UPDATE ON self_model_snapshots
FOR EACH ROW
WHEN 
    NEW.id != OLD.id OR
    NEW.snapshot_at != OLD.snapshot_at OR
    NEW.cycle_number != OLD.cycle_number OR
    NEW.schema_version != OLD.schema_version OR
    NEW.total_improvement_cycles IS NOT OLD.total_improvement_cycles OR
    NEW.deployed_improvements IS NOT OLD.deployed_improvements OR
    NEW.rolled_back_improvements IS NOT OLD.rolled_back_improvements OR
    NEW.overall_deploy_rate IS NOT OLD.overall_deploy_rate OR
    NEW.active_failure_patterns IS NOT OLD.active_failure_patterns OR
    NEW.resolved_failure_patterns IS NOT OLD.resolved_failure_patterns OR
    NEW.root_cause_breakdown_json IS NOT OLD.root_cause_breakdown_json OR
    NEW.active_architectural_patterns IS NOT OLD.active_architectural_patterns OR
    NEW.resolved_architectural_patterns IS NOT OLD.resolved_architectural_patterns OR
    NEW.open_hypotheses IS NOT OLD.open_hypotheses OR
    NEW.implemented_hypotheses IS NOT OLD.implemented_hypotheses OR
    NEW.active_rules IS NOT OLD.active_rules OR
    NEW.candidate_rules IS NOT OLD.candidate_rules OR
    NEW.deprecated_rules IS NOT OLD.deprecated_rules OR
    NEW.top_rule_confidence IS NOT OLD.top_rule_confidence OR
    NEW.refinement_chains_active IS NOT OLD.refinement_chains_active OR
    NEW.total_evolution_runs IS NOT OLD.total_evolution_runs OR
    NEW.strategy_win_rates_json IS NOT OLD.strategy_win_rates_json OR
    NEW.mutation_win_rates_json IS NOT OLD.mutation_win_rates_json OR
    NEW.avg_candidates_per_run IS NOT OLD.avg_candidates_per_run OR
    NEW.population_sizes_json IS NOT OLD.population_sizes_json OR
    NEW.predictor_summary_json IS NOT OLD.predictor_summary_json OR
    NEW.active_predictor_count IS NOT OLD.active_predictor_count
BEGIN
    SELECT RAISE(ABORT, 'self_model_snapshots table is append-only except for Phase 6 counter columns.');
END;

CREATE TRIGGER IF NOT EXISTS prevent_self_model_snapshots_delete
BEFORE DELETE ON self_model_snapshots
BEGIN
    SELECT RAISE(ABORT, 'self_model_snapshots table is append-only.');
END;
