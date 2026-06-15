import os
import time
import sqlite3
import json

from aria.metrics.db import init_db, get_connection
from aria.memory.schema import run_migrations

def reset_db():
    if os.path.exists("aria.db"):
        os.remove("aria.db")
    init_db("aria.db")
    run_migrations("aria.db")

def test_min_evidence():
    print("Testing MIN_EVIDENCE Threshold...")
    with get_connection() as conn:
        conn.execute("INSERT INTO root_cause_clusters (root_cause_category, cluster_label, pattern_ids, tool_names, total_occurrences, similarity_threshold) VALUES (?, ?, ?, ?, ?, ?)", ("Network", None, "[1,2]", '["t1", "t2"]', 3, 0.60))
        
    from aria.rootcause.pattern_extraction import extract_architectural_patterns
    stats = extract_architectural_patterns("aria.db")
    assert stats["patterns_created"] == 0, "Should not create pattern for total_occurrences < 4"
    
    with get_connection() as conn:
        conn.execute("UPDATE root_cause_clusters SET total_occurrences = 4")
        
    stats = extract_architectural_patterns("aria.db")
    assert stats["patterns_created"] == 1, "Should create pattern for total_occurrences == 4"
    print("MIN_EVIDENCE Test passed.")
    
def test_single_tool_filtering():
    print("Testing Single Tool Filtering...")
    with get_connection() as conn:
        conn.execute("INSERT INTO root_cause_clusters (root_cause_category, cluster_label, pattern_ids, tool_names, total_occurrences, similarity_threshold) VALUES (?, ?, ?, ?, ?, ?)", ("Network", None, "[3,4]", '["t1"]', 10, 0.60))
        
    from aria.rootcause.pattern_extraction import extract_architectural_patterns
    stats = extract_architectural_patterns("aria.db")
    assert stats["patterns_created"] == 0, "Should not create pattern for single-tool cluster"
    print("Single Tool Filtering passed.")

def test_idempotency():
    print("Testing Idempotency...")
    from aria.rootcause.pattern_extraction import extract_architectural_patterns
    stats = extract_architectural_patterns("aria.db")
    assert stats["patterns_created"] == 0
    assert stats["patterns_updated"] == 0
    print("Idempotency passed.")

def test_llm_validation():
    print("Testing LLM output validation and fallback...")
    from aria.rootcause.pattern_extraction import _generate_pattern_with_llm
    from unittest.mock import patch
    
    with patch("aria.rootcause.pattern_extraction.Groq") as MockGroq:
        MockGroq.side_effect = Exception("Mock LLM Failure")
        name, desc = _generate_pattern_with_llm("Network", ["search", "weather"], ["test"], "Network category")
    
    # It should fallback gracefully
    assert "Network issue affecting search, weather" in name
    assert "search, weather" in desc
    print("LLM Validation passed.")
    
def test_self_model_injection():
    print("Testing Self Model Injection...")
    from aria.introspection.meta import run_meta_introspection
    
    # run it with 0 cycles just to trigger the payload generation
    run_meta_introspection(n_cycles=0)
    
    with open("self_model.json", "r") as f:
        sm = json.load(f)
        
    assert "architectural_patterns" in sm
    patterns = sm["architectural_patterns"]
    assert len(patterns) == 1
    assert isinstance(patterns[0]["pattern_name"], str)
    assert sorted(patterns[0]["affected_tools"]) == ["t1", "t2"]
    print("Self Model Injection passed.")

if __name__ == "__main__":
    reset_db()
    test_min_evidence()
    test_single_tool_filtering()
    test_idempotency()
    test_llm_validation()
    test_self_model_injection()
    print("ALL TESTS PASSED!")
