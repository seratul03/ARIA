import pytest
import sqlite3
import json
from aria.reflection.mistakes import detect_recurring_mistakes, RULE_VIOLATION_RATE, OSCILLATION_FRACTION, MALFORM_RATE, REGRESSION_CLUSTER_THRESHOLD, PARENT_REUSE_THRESHOLD

SCHEMA = """
CREATE TABLE self_model_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER, recurring_mistake_count INTEGER DEFAULT 0);
CREATE TABLE engineering_rules (id INTEGER PRIMARY KEY, rule_text TEXT, status TEXT);
CREATE TABLE rule_applications (id INTEGER PRIMARY KEY, rule_id INTEGER, cycle_id TEXT, improvement_history_id INTEGER);
CREATE TABLE improvement_history (id INTEGER PRIMARY KEY, tool_name TEXT, result TEXT, weakness_category TEXT);
CREATE TABLE evolution_runs (id INTEGER PRIMARY KEY, tool_name TEXT, run_status TEXT, cycle_id TEXT, winner_candidate_id INTEGER);
CREATE TABLE evolution_candidates (id INTEGER PRIMARY KEY, evolution_run_id INTEGER, strategy TEXT, rule_compliance_score REAL, disqualified INTEGER, disqualification_reason TEXT);
CREATE TABLE hypotheses (id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, status TEXT, attempt_count INTEGER);
CREATE TABLE root_cause_clusters (id INTEGER PRIMARY KEY, root_cause_category TEXT);
CREATE TABLE recurring_mistakes (id INTEGER PRIMARY KEY AUTOINCREMENT, mistake_type TEXT, description TEXT, evidence_json TEXT, occurrence_count INTEGER DEFAULT 1, first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP, last_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP, status TEXT DEFAULT 'active', snapshot_id INTEGER);
"""

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_mistakes.db"
    
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    
    # Pre-populate basic dependencies
    conn.execute("INSERT INTO self_model_snapshots (id, cycle_number) VALUES (1, 100)")
    
    # Let's seed 10 completed evolution runs and their candidates
    for i in range(1, 11):
        tool = 'target_tool' if i <= 5 else 'other_tool'
        conn.execute("INSERT INTO improvement_history (id, tool_name, result, weakness_category) VALUES (?, ?, 'deployed', 'network_errors')", (i, tool))
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id, winner_candidate_id) VALUES (?, ?, 'completed', ?, ?)", (i, tool, f"cycle_{i}", i))
        # Valid strategy, compliance=1.0 initially
        conn.execute("INSERT INTO evolution_candidates (id, evolution_run_id, strategy, rule_compliance_score, disqualified) VALUES (?, ?, 'mutation', 1.0, 0)", (i, i))
        
    conn.commit()
    conn.close()
    
    return str(db_path)

def test_detect_no_mistakes(test_db):
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] == 0
    assert stats["resolved"] == 0

def test_rule_violation_pattern(test_db):
    conn = sqlite3.connect(test_db)
    # Insert an active rule
    conn.execute("INSERT INTO engineering_rules (id, rule_text, status) VALUES (1, 'Never use eval', 'active')")
    
    # Make it applicable in 10 cycles, but violated in 6 (compliance=0.0) -> 60% > RULE_VIOLATION_RATE
    for i in range(1, 11):
        conn.execute("INSERT INTO rule_applications (id, rule_id, cycle_id, improvement_history_id) VALUES (?, 1, ?, ?)", (i, f"cycle_{i}", i))
        if i <= 6:
            conn.execute("UPDATE evolution_candidates SET rule_compliance_score = 0.0 WHERE id = ?", (i,))
            
    conn.commit()
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] == 1
    
    mistake = conn.execute("SELECT * FROM recurring_mistakes WHERE mistake_type = 'rule_violation_pattern'").fetchone()
    assert mistake is not None
    evidence = json.loads(mistake[3])
    assert evidence["violation_rate"] == 0.6
    assert evidence["rule_id"] == 1

def test_target_selection_oscillation_resolves(test_db):
    conn = sqlite3.connect(test_db)
    conn.execute("DELETE FROM evolution_runs")
    conn.execute("DELETE FROM improvement_history")
    for i in range(1, 11):
        conn.execute("INSERT INTO improvement_history (id, tool_name, result, weakness_category) VALUES (?, 'target_tool', 'failed', 'network_errors')", (i,))
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (?, 'target_tool', 'completed', ?)", (i, f"cycle_{i}"))
    conn.commit()
    
    # All 10 runs are for 'target_tool' and no recent deployments
    # 10/10 selections = 100% > OSCILLATION_FRACTION (60%)
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] == 1
    mistake_id = conn.execute("SELECT id FROM recurring_mistakes WHERE mistake_type = 'target_selection_oscillation'").fetchone()[0]
    
    # Now advance 5 cycles where target_tool is NOT targeted
    for i in range(11, 16):
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (?, 'other_tool', 'completed', ?)", (i, f"cycle_{i}"))
        
    conn.commit()
    
    # Re-run detection over last 10? No, lookback is 20. Total runs = 15.
    # target_tool = 10/15 = 66.6%... still > 60%.
    # Add 2 more
    conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (16, 'other_tool', 'completed', 'cycle_16')")
    conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (17, 'other_tool', 'completed', 'cycle_17')")
    conn.commit()
    
    # Now target_tool = 10/17 = 58% < 60%. Should auto-resolve.
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] == 0
    assert stats["resolved"] == 1
    
    mistake = conn.execute("SELECT status FROM recurring_mistakes WHERE id = ?", (mistake_id,)).fetchone()
    assert mistake[0] == 'resolved'

def test_short_lookback_graceful(test_db):
    # If we only have 5 runs in DB, does it crash or work?
    conn = sqlite3.connect(test_db)
    conn.execute("DELETE FROM evolution_runs")
    conn.execute("DELETE FROM improvement_history")
    for i in range(1, 6):
        conn.execute("INSERT INTO improvement_history (id, tool_name, result, weakness_category) VALUES (?, 'target_tool', 'failed', 'network_errors')", (i,))
        conn.execute("INSERT INTO evolution_runs (id, tool_name, run_status, cycle_id) VALUES (?, 'target_tool', 'completed', ?)", (i, f"cycle_{i}"))
    conn.commit()
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    # Works gracefully. The 5 runs are for 'target_tool' -> 5/5 = 100% oscillation.
    assert stats["detected"] == 1

def test_malformed_code_recurrence(test_db):
    conn = sqlite3.connect(test_db)
    # Total 10 runs. Let's make 3 of them malformed (> 25% threshold)
    for i in range(1, 4):
        conn.execute("UPDATE evolution_candidates SET disqualified = 1, disqualification_reason = 'static_analysis_failed' WHERE id = ?", (i,))
    conn.commit()
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] > 0
    
    mistake = conn.execute("SELECT * FROM recurring_mistakes WHERE mistake_type = 'malformed_code_recurrence'").fetchone()
    assert mistake is not None
    ev = json.loads(mistake[3])
    assert ev["malform_rate"] == 0.3

def test_breeding_parent_reuse(test_db):
    conn = sqlite3.connect(test_db)
    # Make 3 candidates have 'bred:42+11' strategy
    conn.execute("UPDATE evolution_candidates SET strategy = 'bred:42+11' WHERE id IN (1, 2, 3)")
    conn.commit()
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    
    rows = conn.execute("SELECT evidence_json FROM recurring_mistakes WHERE mistake_type = 'breeding_parent_reuse'").fetchall()
    assert len(rows) == 2  # 42 and 11
    
    ev_42 = json.loads(rows[0][0])
    ev_11 = json.loads(rows[1][0])
    
    assert ev_42["reuse_count"] == 3
    assert ev_11["reuse_count"] == 3

def test_regression_cluster(test_db):
    conn = sqlite3.connect(test_db)
    # Set 4 rollbacks out of 10 to 'network_errors'
    for i in range(1, 5):
        conn.execute("UPDATE improvement_history SET result = 'rolled_back' WHERE id = ?", (i,))
        
    conn.commit()
    
    stats = detect_recurring_mistakes(test_db, snapshot_id=1, lookback_cycles=20)
    assert stats["detected"] > 0
    
    row = conn.execute("SELECT evidence_json FROM recurring_mistakes WHERE mistake_type = 'post_deploy_regression_cluster'").fetchone()
    assert row is not None
    ev = json.loads(row[0])
    assert ev["category"] == "network_errors"
    assert ev["regression_rate"] == 1.0  # 4 rollbacks, all 4 are network_errors!
