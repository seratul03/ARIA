import pytest
import sqlite3
import json
from aria.reflection.prompts import (
    estimate_prompt_section_lengths,
    detect_bad_prompts,
    DIRECTIVE_IGNORE_RATE, CONTEXT_BUDGET_TOKENS, OVERFLOW_CORRELATION_THRESHOLD,
    MIN_SUMMARY_WORDS, MAX_SUMMARY_WORDS, ANOMALY_RATE, SKEW_THRESHOLD
)

SCHEMA = """
CREATE TABLE self_model_snapshots (id INTEGER PRIMARY KEY AUTOINCREMENT, cycle_number INTEGER);
CREATE TABLE evolution_runs (id INTEGER PRIMARY KEY, tool_name TEXT, run_status TEXT, cycle_id INTEGER, winner_candidate_id INTEGER);
CREATE TABLE evolution_candidates (id INTEGER PRIMARY KEY, evolution_run_id INTEGER, strategy TEXT, rule_compliance_score REAL, composite_score REAL, disqualified INTEGER);
CREATE TABLE improvement_history (id INTEGER PRIMARY KEY, tool_name TEXT, fix_summary TEXT, result TEXT, timestamp DATETIME, improvement_type TEXT);
CREATE TABLE failure_patterns (id INTEGER PRIMARY KEY, classification_source TEXT, root_cause_category TEXT, status TEXT);
CREATE TABLE bad_prompt_findings (id INTEGER PRIMARY KEY AUTOINCREMENT, prompt_type TEXT, finding_type TEXT, description TEXT, evidence_json TEXT, correlation_metric REAL, status TEXT DEFAULT 'active', last_updated_at DATETIME, snapshot_id INTEGER);
CREATE TABLE cycle_traces (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME);
"""

@pytest.fixture
def test_db(tmp_path):
    db_path = tmp_path / "test_prompts.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA)
    conn.execute("INSERT INTO self_model_snapshots (id, cycle_number) VALUES (1, 100)")
    conn.commit()
    conn.close()
    return str(db_path)

def test_estimate_prompt_section_lengths(test_db):
    # Should use fallback without prompt_text
    lens = estimate_prompt_section_lengths(test_db, 1)
    assert lens["memory"] == 1500
    assert lens["directive"] == 400
    assert lens["engineering_principles"] == 1000
    assert lens["generation_directive"] == 300

def test_strategy_directive_ignored(test_db):
    conn = sqlite3.connect(test_db)
    # 10 cases for RULE_GUIDED, 5 < 0.30 (50% > 40%) -> Should trigger
    for i in range(10):
        score = 0.1 if i < 5 else 0.8
        conn.execute("INSERT INTO evolution_candidates (evolution_run_id, strategy, rule_compliance_score) VALUES (1, 'RULE_GUIDED_1', ?)", (score,))
    
    # 10 cases for RULE_GUIDED_2, 2 < 0.30 (20% < 40%) -> Should NOT trigger
    for i in range(10):
        score = 0.1 if i < 2 else 0.8
        conn.execute("INSERT INTO evolution_candidates (evolution_run_id, strategy, rule_compliance_score) VALUES (1, 'RULE_GUIDED_2', ?)", (score,))
    conn.commit()
    
    stats = detect_bad_prompts(test_db, 1)
    
    rows = conn.execute("SELECT evidence_json FROM bad_prompt_findings WHERE finding_type = 'strategy_directive_ignored'").fetchall()
    assert len(rows) == 1
    ev = json.loads(rows[0][0])
    assert ev["strategy"] == "RULE_GUIDED_1"
    assert ev["ignored_cases"] == 5

def test_classification_category_skew(test_db):
    conn = sqlite3.connect(test_db)
    # Heuristic: evenly distributed (3 categories, 3 each)
    for cat in ['Logic', 'Syntax', 'Timeout']:
        for _ in range(3):
            conn.execute("INSERT INTO failure_patterns (classification_source, root_cause_category, status) VALUES ('heuristic', ?, 'active')", (cat,))
            
    # LLM: heavily skewed towards Logic (7/10 = 70% > 60%)
    for i in range(10):
        cat = 'Logic' if i < 7 else 'Syntax'
        conn.execute("INSERT INTO failure_patterns (classification_source, root_cause_category, status) VALUES ('llm', ?, 'active')", (cat,))
        
    conn.commit()
    stats = detect_bad_prompts(test_db, 1)
    
    row = conn.execute("SELECT evidence_json FROM bad_prompt_findings WHERE finding_type = 'classification_category_skew'").fetchone()
    assert row is not None
    ev = json.loads(row[0])
    assert ev["skewed_category"] == "Logic"
    assert ev["llm_fraction"] == 0.7
    assert ev["heuristic_fraction"] == 1/3
