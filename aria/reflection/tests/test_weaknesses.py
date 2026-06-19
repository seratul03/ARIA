import pytest
import sqlite3
import json
from aria.reflection.weaknesses import detect_architectural_weaknesses, MAX_HYPOTHESIS_ATTEMPTS

SCHEMA = """
CREATE TABLE self_model_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER, overall_deploy_rate REAL, active_weaknesses INTEGER DEFAULT 0, predictor_summary_json TEXT);
CREATE TABLE failure_history (id INTEGER PRIMARY KEY, memory_score REAL);
CREATE TABLE failure_patterns (id INTEGER PRIMARY KEY, traceback_signature TEXT, representative_failure_id INTEGER, tool_names TEXT, occurrence_count INTEGER, first_seen DATETIME, last_seen DATETIME, status TEXT);
CREATE TABLE root_cause_clusters (id INTEGER PRIMARY KEY, root_cause_category TEXT, cluster_label TEXT, pattern_ids TEXT, tool_names TEXT, total_occurrences INTEGER, similarity_threshold REAL);
CREATE TABLE hypotheses (id INTEGER PRIMARY KEY, source_type TEXT, source_id INTEGER, root_cause_summary TEXT, proposed_fix_summary TEXT, target_tools TEXT, status TEXT, attempt_count INTEGER);
CREATE TABLE evolution_candidates (id INTEGER PRIMARY KEY, evolution_run_id INTEGER, strategy_name TEXT, candidate_type TEXT);
CREATE TABLE evolution_runs (id INTEGER PRIMARY KEY, tool_name TEXT, run_status TEXT, started_at DATETIME, trigger_type TEXT, hypothesis_id INTEGER, winner_candidate_id INTEGER);
CREATE TABLE improvement_history (id INTEGER PRIMARY KEY, result TEXT);
CREATE TABLE predictor_registry (id INTEGER PRIMARY KEY, predictor_type TEXT, version INTEGER, model_path TEXT, feature_schema_hash TEXT, train_samples INTEGER, test_samples INTEGER, test_accuracy REAL, status TEXT, notes TEXT);
CREATE TABLE engineering_rules (id INTEGER PRIMARY KEY, rule_text TEXT, category TEXT, source_type TEXT, source_id INTEGER, initial_confidence REAL, status TEXT);
CREATE TABLE architectural_weaknesses (id INTEGER PRIMARY KEY AUTOINCREMENT, weakness_type TEXT, title TEXT, evidence_json TEXT, severity TEXT, status TEXT, first_detected_at DATETIME, last_updated_at DATETIME, addressed_by_proposal_id INTEGER, snapshot_id INTEGER);
"""

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_weaknesses.db"
    
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    
    # Pre-populate data for self_model_snapshots
    conn.execute("""
        INSERT INTO self_model_snapshots 
        (cycle_number, overall_deploy_rate, active_weaknesses, predictor_summary_json)
        VALUES (10, 0.80, 0, ?)
    """, (json.dumps({
        "success": {"status": "active", "test_accuracy": 0.85, "actual_accuracy": 0.70}  # drift!
    }),))
    
    # 1. category_blind_spot
    conn.execute("""
        INSERT INTO failure_history (id, memory_score) VALUES (1, 0.10)
    """)
    conn.execute("""
        INSERT INTO failure_patterns (id, traceback_signature, representative_failure_id, tool_names, occurrence_count, first_seen, last_seen, status)
        VALUES (1, 'sig1', 1, '[]', 6, '2023-01-01', '2023-01-01', 'active')
    """) # also triggers memory_rot!
    conn.execute("""
        INSERT INTO root_cause_clusters (id, root_cause_category, cluster_label, pattern_ids, tool_names, total_occurrences, similarity_threshold)
        VALUES (1, 'network_timeout', 'Timeout', '[1]', '[]', 6, 0.9)
    """)
    
    # 3. hypothesis_stall
    conn.execute("""
        INSERT INTO hypotheses (id, source_type, source_id, root_cause_summary, proposed_fix_summary, target_tools, status, attempt_count)
        VALUES (1, 'cluster', 1, 'x', 'y', '[]', 'proposed', ?)
    """, (MAX_HYPOTHESIS_ATTEMPTS,))
    
    # 4. population_collapse
    for i in range(1, 6):
        conn.execute(f"""
            INSERT INTO evolution_candidates (id, evolution_run_id, strategy_name, candidate_type) 
            VALUES ({i}, {i}, 'BruteForce', 'foo')
        """)
        conn.execute(f"""
            INSERT INTO evolution_runs (id, tool_name, run_status, started_at, trigger_type, hypothesis_id, winner_candidate_id)
            VALUES ({i}, 'test_tool', 'completed', '2023-01-01', 'x', 1, {i})
        """)
        
    # 7. self_model_lag (5 deployed, 5 failed -> 50% rolling)
    for i in range(1, 6):
        conn.execute(f"INSERT INTO improvement_history (id, result) VALUES ({i}, 'deployed')")
    for i in range(6, 11):
        conn.execute(f"INSERT INTO improvement_history (id, result) VALUES ({i}, 'failed_sandbox')")
        
    # 8. token_concentration
    conn.execute("""
        INSERT INTO predictor_registry (id, predictor_type, version, model_path, feature_schema_hash, train_samples, test_samples, test_accuracy, status, notes)
        VALUES (1, 'success', 1, 'x', 'x', 1, 1, 0.9, 'active', ?)
    """, (json.dumps({
        "top_10_features": [{"feature": "foo", "importance": 0.60}]
    }),))

    conn.commit()
    conn.close()
    
    yield str(db_path)

def test_detect_architectural_weaknesses(test_db):
    stats = detect_architectural_weaknesses(test_db, snapshot_id=1)
    assert stats["detected"] > 0
    assert stats["updated"] == 0
    assert stats["resolved"] == 0
    
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM architectural_weaknesses WHERE status = 'active'").fetchall()
    
    detected_types = {r["weakness_type"] for r in rows}
    assert "category_blind_spot" in detected_types
    assert "predictor_drift" in detected_types
    assert "hypothesis_stall" in detected_types
    assert "population_collapse" in detected_types
    assert "memory_rot" in detected_types
    assert "self_model_lag" in detected_types
    assert "token_concentration" in detected_types
    # rule_coverage_gap is superseded by category_blind_spot in this specific db setup, so it shouldn't be present
    assert "rule_coverage_gap" not in detected_types

    # Test idempotency
    stats2 = detect_architectural_weaknesses(test_db, snapshot_id=1)
    assert stats2["detected"] == 0
    assert stats2["updated"] > 0
    assert stats2["resolved"] == 0

def test_self_healing(test_db):
    detect_architectural_weaknesses(test_db, snapshot_id=1)
    
    conn = sqlite3.connect(test_db)
    # Fix category_blind_spot by adding an active engineering rule
    conn.execute("""
        INSERT INTO engineering_rules (rule_text, category, source_type, source_id, initial_confidence, status)
        VALUES ('x', 'network_timeout', 'cluster', 1, 0.9, 'active')
    """)
    conn.commit()
    conn.close()
    
    stats = detect_architectural_weaknesses(test_db, snapshot_id=1)
    assert stats["resolved"] >= 1  # at least category_blind_spot resolved
    
    conn = sqlite3.connect(test_db)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM architectural_weaknesses WHERE status = 'active'").fetchall()
    detected_types = {r["weakness_type"] for r in rows}
    
    assert "category_blind_spot" not in detected_types
    assert "rule_coverage_gap" not in detected_types
