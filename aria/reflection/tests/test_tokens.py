import pytest
import sqlite3
import json
from aria.reflection.tokens import (
    ensure_token_tracking,
    analyze_token_waste,
    DISQ_WASTE_THRESHOLD, RETRIEVAL_AGE_THRESHOLD, BARREN_CYCLE_RATE
)

SCHEMA = """
CREATE TABLE self_model_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER);
CREATE TABLE evolution_runs (id INTEGER PRIMARY KEY, tool_name TEXT, run_status TEXT, cycle_id INTEGER, winner_candidate_id INTEGER);
CREATE TABLE evolution_candidates (id INTEGER PRIMARY KEY, evolution_run_id INTEGER, disqualified INTEGER);
CREATE TABLE improvement_history (id INTEGER PRIMARY KEY, tool_name TEXT, result TEXT, timestamp DATETIME, improvement_type TEXT);
CREATE TABLE failure_patterns (id INTEGER PRIMARY KEY, classification_source TEXT, status TEXT);
CREATE TABLE token_waste_findings (id INTEGER PRIMARY KEY AUTOINCREMENT, waste_type TEXT, description TEXT, estimated_tokens_wasted_per_cycle REAL, evidence_json TEXT, severity TEXT, status TEXT DEFAULT 'active', last_updated_at DATETIME, snapshot_id INTEGER);
CREATE TABLE cycle_traces (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME);
"""

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_tokens.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO self_model_snapshots (id, cycle_number) VALUES (1, 100)")
    conn.commit()
    conn.close()
    return str(db_path)

def test_ensure_token_tracking(test_db):
    assert ensure_token_tracking(test_db) is True
    # Verify column was added
    conn = sqlite3.connect(test_db)
    cursor = conn.execute("PRAGMA table_info(cycle_traces)")
    cols = [row[1] for row in cursor.fetchall()]
    assert "tokens_used" in cols
    
    # Running it again should not crash
    assert ensure_token_tracking(test_db) is True

def test_high_disqualification_generation(test_db):
    conn = sqlite3.connect(test_db)
    # Bad run: 10 candidates, 5 disqualified (50% > 40%)
    conn.execute("INSERT INTO evolution_runs (id, run_status) VALUES (1, 'completed')")
    for i in range(10):
        disq = 1 if i < 5 else 0
        conn.execute("INSERT INTO evolution_candidates (evolution_run_id, disqualified) VALUES (1, ?)", (disq,))
    conn.commit()
    
    stats = analyze_token_waste(test_db, 1)
    
    row = conn.execute("SELECT evidence_json FROM token_waste_findings WHERE waste_type = 'high_disqualification_generation'").fetchone()
    assert row is not None
    ev = json.loads(row[0])
    assert ev["affected_runs_count"] == 1
    assert ev["total_disqualified"] == 5

def test_low_yield_meta_cycle(test_db):
    conn = sqlite3.connect(test_db)
    # 10 meta cycles, 4 rejected/rolled_back (40% > 30%)
    for i in range(10):
        res = 'rejected' if i < 4 else 'deployed'
        conn.execute("INSERT INTO improvement_history (improvement_type, result, timestamp) VALUES ('meta', ?, CURRENT_TIMESTAMP)", (res,))
    conn.commit()
    
    stats = analyze_token_waste(test_db, 1)
    
    row = conn.execute("SELECT evidence_json FROM token_waste_findings WHERE waste_type = 'low_yield_meta_cycle'").fetchone()
    assert row is not None
    ev = json.loads(row[0])
    assert ev["barren_fraction"] == 0.4
