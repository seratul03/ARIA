import sqlite3
import pytest
import tempfile
import os
import json
from pathlib import Path
from aria.reflection.self_model import build_self_model_snapshot, get_latest_snapshot, get_snapshot_trend, export_self_model_json

SCHEMA = """
CREATE TABLE IF NOT EXISTS self_model_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cycle_number    INTEGER NOT NULL,
    schema_version  INTEGER NOT NULL,

    total_improvement_cycles        INTEGER,
    deployed_improvements           INTEGER,
    rolled_back_improvements        INTEGER,
    overall_deploy_rate             REAL,
    active_failure_patterns         INTEGER,
    resolved_failure_patterns       INTEGER,

    root_cause_breakdown_json       TEXT,
    active_architectural_patterns   INTEGER,
    resolved_architectural_patterns INTEGER,
    open_hypotheses                 INTEGER,
    implemented_hypotheses          INTEGER,

    active_rules                    INTEGER,
    candidate_rules                 INTEGER,
    deprecated_rules                INTEGER,
    top_rule_confidence             REAL,
    refinement_chains_active        INTEGER,

    total_evolution_runs            INTEGER,
    strategy_win_rates_json         TEXT,
    mutation_win_rates_json         TEXT,
    avg_candidates_per_run          REAL,
    population_sizes_json           TEXT,

    predictor_summary_json          TEXT,
    active_predictor_count          INTEGER,

    active_weaknesses               INTEGER DEFAULT 0,
    resolved_weaknesses             INTEGER DEFAULT 0,
    recurring_mistake_count         INTEGER DEFAULT 0,
    open_proposals                  INTEGER DEFAULT 0,
    implemented_proposals           INTEGER DEFAULT 0,
    failed_proposals                INTEGER DEFAULT 0
);

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
"""

@pytest.fixture
def test_db():
    fd, path = tempfile.mkstemp()
    os.close(fd)
    conn = sqlite3.connect(path)
    # create the tables needed to avoid OperationalError
    conn.executescript(SCHEMA)
    conn.executescript('''
        CREATE TABLE improvement_history(id INTEGER, result TEXT);
        INSERT INTO improvement_history VALUES(1, 'deployed');
        INSERT INTO improvement_history VALUES(2, 'failed');
    ''')
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)

def test_build_and_get_snapshot(test_db):
    build_self_model_snapshot(test_db, cycle_number=1)
    snapshot = get_latest_snapshot(test_db)
    
    assert snapshot is not None
    assert snapshot["cycle_number"] == 1
    assert snapshot["total_improvement_cycles"] == 2
    assert snapshot["deployed_improvements"] == 1
    assert snapshot["overall_deploy_rate"] == 0.5
    assert snapshot["active_weaknesses"] == 0

def test_get_snapshot_trend(test_db):
    build_self_model_snapshot(test_db, cycle_number=1)
    build_self_model_snapshot(test_db, cycle_number=2)
    
    trend = get_snapshot_trend(test_db, "overall_deploy_rate", last_n=5)
    assert len(trend) == 2
    assert trend[0] == (1, 0.5)
    assert trend[1] == (2, 0.5)

def test_export_self_model_json(test_db):
    build_self_model_snapshot(test_db, cycle_number=1)
    
    fd, out_path = tempfile.mkstemp()
    os.close(fd)
    
    try:
        data = export_self_model_json(test_db, out_path)
        assert "snapshot" in data
        assert data["snapshot"]["cycle_number"] == 1
        
        with open(out_path, 'r') as f:
            written_data = json.load(f)
            assert written_data["snapshot"]["cycle_number"] == 1
    finally:
        os.unlink(out_path)

def test_append_only_trigger(test_db):
    build_self_model_snapshot(test_db, cycle_number=1)
    
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM self_model_snapshots LIMIT 1").fetchone()
    
    # Allowed update (Phase 6 column)
    conn.execute("UPDATE self_model_snapshots SET active_weaknesses = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    
    # Disallowed update
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE self_model_snapshots SET total_improvement_cycles = 100 WHERE id = ?", (row["id"],))
    
    conn.close()
