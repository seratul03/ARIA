import os
import json
from unittest.mock import patch, MagicMock

from aria.metrics.db import init_db, get_connection
from aria.memory.schema import run_migrations
from aria.rootcause.report import generate_root_cause_report

def reset_db():
    if os.path.exists("aria.db"):
        os.remove("aria.db")
    init_db("aria.db")
    run_migrations("aria.db")

def populate_test_data():
    with get_connection() as conn:
        # Clusters
        for i in range(10):
            conn.execute(
                "INSERT INTO root_cause_clusters (root_cause_category, total_occurrences, similarity_threshold, tool_names, pattern_ids) VALUES (?, ?, ?, ?, ?)",
                (f"Cat{i}", 10-i, 0.5, '["tool1"]', '[1]')
            )
            
        # Architectural Patterns
        for i in range(10):
            conn.execute(
                "INSERT INTO architectural_patterns (cluster_id, pattern_name, description, affected_tools, evidence_count, status) VALUES (?, ?, ?, ?, ?, ?)",
                (1, f"Pattern{i}", "desc", '["tool1"]', 10-i, "active")
            )
            
        # Improvement history simulating fix -> rollback -> fix -> held
        
        # Tool1: Fix 1 (holds)
        id1 = conn.execute(
            "INSERT INTO improvement_history (cycle_id, improvement_type, tool_name, result, problem_description, fix_summary, baseline_fitness, candidate_fitness, fitness_delta, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (1, "tool", "tool1", "deployed", "p", "f", 0.5, 0.9, 0.4, 100)
        ).lastrowid
        
        # Tool2: Fix 2 (rolled back later)
        id2 = conn.execute(
            "INSERT INTO improvement_history (cycle_id, improvement_type, tool_name, result, problem_description, fix_summary, baseline_fitness, candidate_fitness, fitness_delta, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (2, "tool", "tool2", "deployed", "p", "f", 0.5, 0.9, 0.4, 100)
        ).lastrowid
        
        # The rollback for Tool2
        conn.execute(
            "INSERT INTO improvement_history (cycle_id, improvement_type, tool_name, result, problem_description, fix_summary, baseline_fitness, candidate_fitness, fitness_delta, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (3, "tool", "tool2", "rolled_back", "p", "rolled back", 0.9, 0.5, -0.4, 200)
        )
        
        # Hypotheses
        # 10 proposed
        for i in range(10):
            conn.execute(
                "INSERT INTO hypotheses (source_type, source_id, root_cause_summary, proposed_fix_summary, target_tools, confidence, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("cluster", 1, "rc", "pf", '["tool1"]', 0.9, "proposed")
            )
            
        # Implemented for tool1 (holds)
        conn.execute(
            "INSERT INTO hypotheses (source_type, source_id, root_cause_summary, proposed_fix_summary, target_tools, confidence, status, resolved_improvement_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("cluster", 1, "rc", "pf", '["tool1"]', 0.9, "implemented", id1)
        )
        
        # Implemented for tool2 (rolled back)
        conn.execute(
            "INSERT INTO hypotheses (source_type, source_id, root_cause_summary, proposed_fix_summary, target_tools, confidence, status, resolved_improvement_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("cluster", 1, "rc", "pf", '["tool2"]', 0.9, "implemented", id2)
        )

def test_no_llm_calls():
    print("Testing generate_root_cause_report(llm_narrative=False) makes zero LLM calls...")
    with patch("groq.Groq") as MockGroq:
        report = generate_root_cause_report("aria.db", llm_narrative=False)
        MockGroq.assert_not_called()
        assert report["narrative"] is None
    print("No LLM calls verified.")

def test_truncation_budget():
    print("Testing Truncation Budget...")
    report = generate_root_cause_report("aria.db", llm_narrative=False)
    
    assert len(report["top_clusters"]) <= 5, f"Top clusters count is {len(report['top_clusters'])}"
    assert len(report["architectural_patterns"]) <= 5, f"Arch patterns count is {len(report['architectural_patterns'])}"
    assert len(report["hypotheses"]["proposed"]) <= 5, f"Proposed hyp count is {len(report['hypotheses']['proposed'])}"
    print("Truncation budget verified.")

def test_fix_durability():
    print("Testing Fix Durability...")
    report = generate_root_cause_report("aria.db", llm_narrative=False)
    
    durability = report["fix_durability"]
    assert durability["held"] == 1, f"Expected 1 held fix, got {durability['held']}"
    assert durability["rolled_back"] == 1, f"Expected 1 rolled back fix, got {durability['rolled_back']}"
    print("Fix durability verified.")

def test_readonly_execution():
    print("Testing Read-Only Execution...")
    original_get_conn = get_connection
    class ConnectionWrapper:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, parameters=()):
            if sql.strip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
                raise ValueError(f"Write operation detected! SQL: {sql}")
            return self.conn.execute(sql, parameters)
            
    class SpyConnection:
        def __init__(self):
            self.conn = original_get_conn()
        def __enter__(self):
            self.ctx = self.conn.__enter__()
            return ConnectionWrapper(self.ctx)
        def __exit__(self, exc_type, exc_val, exc_tb):
            return self.conn.__exit__(exc_type, exc_val, exc_tb)
            
    with patch("aria.rootcause.report.get_connection", return_value=SpyConnection()):
        generate_root_cause_report("aria.db", llm_narrative=False)
    
    print("Read-only execution verified.")

if __name__ == "__main__":
    reset_db()
    populate_test_data()
    test_no_llm_calls()
    test_truncation_budget()
    test_fix_durability()
    test_readonly_execution()
    print("ALL TESTS PASSED!")
