import pytest
import sqlite3
import json
from aria.reflection.effectiveness import (
    detect_ineffective_improvements,
    STRATEGY_EVAL_RUNS, REJECTION_WINDOW, MIN_APPLICATIONS_DORMANT, 
    MIN_APPLICATIONS_FOR_REFINEMENT, BREEDING_EVAL_RUNS
)

SCHEMA = """
CREATE TABLE self_model_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER);
CREATE TABLE engineering_rules (id INTEGER PRIMARY KEY, rule_text TEXT, status TEXT, scope TEXT);
CREATE TABLE rule_applications (id INTEGER PRIMARY KEY, rule_id INTEGER, improvement_history_id INTEGER);
CREATE TABLE improvement_history (id INTEGER PRIMARY KEY, tool_name TEXT, result TEXT);
CREATE TABLE evolution_runs (id INTEGER PRIMARY KEY, tool_name TEXT, run_status TEXT, cycle_id INTEGER, winner_candidate_id INTEGER);
CREATE TABLE evolution_candidates (id INTEGER PRIMARY KEY, evolution_run_id INTEGER, strategy TEXT);
CREATE TABLE hypotheses (id INTEGER PRIMARY KEY, status TEXT, attempt_count INTEGER, created_at DATETIME);
CREATE TABLE ineffective_improvements (id INTEGER PRIMARY KEY AUTOINCREMENT, ineffectiveness_type TEXT, scope TEXT, metric_name TEXT, metric_value REAL, metric_baseline REAL, evidence_json TEXT, status TEXT DEFAULT 'active', last_updated_at DATETIME, snapshot_id INTEGER);
"""

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_effectiveness.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO self_model_snapshots (id, cycle_number) VALUES (1, 100)")
    conn.commit()
    conn.close()
    return str(db_path)

def test_perpetually_rejected_tool(test_db):
    conn = sqlite3.connect(test_db)
    
    # Tool 1: 10 rejections (detected)
    for i in range(1, REJECTION_WINDOW + 1):
        conn.execute("INSERT INTO improvement_history (id, tool_name, result) VALUES (?, 'bad_tool', 'rejected')", (i,))
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (?, 'bad_tool', 'completed', ?)", (i, i))
        
    # Tool 2: 9 rejections, 1 deployed (not detected)
    offset = REJECTION_WINDOW + 1
    for i in range(offset, offset + REJECTION_WINDOW):
        result = 'deployed' if i == offset + REJECTION_WINDOW - 1 else 'rejected'
        conn.execute("INSERT INTO improvement_history (id, tool_name, result) VALUES (?, 'mixed_tool', ?)", (i, result))
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (?, 'mixed_tool', 'completed', ?)", (i, i))

    conn.commit()
    
    stats = detect_ineffective_improvements(test_db, 1)
    
    rows = conn.execute("SELECT scope FROM ineffective_improvements WHERE ineffectiveness_type = 'perpetually_rejected_tool' AND status = 'active'").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 'tool:bad_tool'

def test_dormant_rule_cross_check(test_db):
    conn = sqlite3.connect(test_db)
    
    # Active rule, 10 applications, 5 successes => success_rate = 0.5 (DORMANT_BAND)
    conn.execute("INSERT INTO engineering_rules (id, rule_text, status, scope) VALUES (7, 'Dormant rule', 'active', NULL)")
    
    for i in range(1, MIN_APPLICATIONS_FOR_REFINEMENT + 1):
        result = 'deployed' if i <= 5 else 'rejected'
        conn.execute("INSERT INTO improvement_history (id, result) VALUES (?, ?)", (i, result))
        conn.execute("INSERT INTO rule_applications (id, rule_id, improvement_history_id) VALUES (?, 7, ?)", (i, i))
        
    conn.commit()
    
    stats = detect_ineffective_improvements(test_db, 1)
    row = conn.execute("SELECT evidence_json FROM ineffective_improvements WHERE ineffectiveness_type = 'dormant_rule'").fetchone()
    assert row is not None
    ev = json.loads(row[0])
    assert ev["refinement_eligible_but_not_yet_refined"] is True
    
    # Test uniqueness of scope strings
    conn.execute("INSERT INTO ineffective_improvements (ineffectiveness_type, scope, metric_name, metric_value, metric_baseline, evidence_json, snapshot_id) VALUES ('perpetually_rejected_tool', 'rule:7', 'dummy', 0, 0, '{}', 1)")
    conn.commit()
    
    # The upsert logic handles it because it filters by ineffectiveness_type AND scope
    stats2 = detect_ineffective_improvements(test_db, 1)
    assert stats2["updated"] == 1 # the dormant_rule was updated

def test_breeding_negative_lift_resolution(test_db):
    conn = sqlite3.connect(test_db)
    
    for i in range(1, BREEDING_EVAL_RUNS + 1):
        winner_id = i * 100
        bred_id = i * 100 + 1
        conn.execute("INSERT INTO evolution_runs (id, run_status, winner_candidate_id) VALUES (?, 'completed', ?)", (i, winner_id))
        conn.execute("INSERT INTO evolution_candidates (id, evolution_run_id, strategy) VALUES (?, ?, 'mutation')", (winner_id, i))
        conn.execute("INSERT INTO evolution_candidates (id, evolution_run_id, strategy) VALUES (?, ?, 'bred:1+2')", (bred_id, i))
        
    conn.commit()
    
    stats = detect_ineffective_improvements(test_db, 1)
    assert stats["detected"] >= 1
    
    # Check that breeding_negative_lift is active
    status = conn.execute("SELECT status FROM ineffective_improvements WHERE ineffectiveness_type = 'breeding_negative_lift'").fetchone()[0]
    assert status == 'active'
    
    # Now, add a new run where breeding won
    next_run = BREEDING_EVAL_RUNS + 1
    winner_id = next_run * 100
    conn.execute("INSERT INTO evolution_runs (id, run_status, winner_candidate_id) VALUES (?, 'completed', ?)", (next_run, winner_id))
    conn.execute("INSERT INTO evolution_candidates (id, evolution_run_id, strategy) VALUES (?, ?, 'bred:3+4')", (winner_id, next_run))
    conn.commit()
    
    # Detect again
    stats2 = detect_ineffective_improvements(test_db, 2)
    
    status2 = conn.execute("SELECT status FROM ineffective_improvements WHERE ineffectiveness_type = 'breeding_negative_lift'").fetchone()[0]
    assert status2 == 'resolved'
