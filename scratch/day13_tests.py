import os
import sqlite3
import json
from unittest.mock import patch, MagicMock

from aria.metrics.db import init_db, get_connection
from aria.memory.schema import run_migrations

def reset_db():
    if os.path.exists("aria.db"):
        os.remove("aria.db")
    init_db("aria.db")
    run_migrations("aria.db")

def test_hypothesis_generation():
    print("Testing Hypothesis Generation...")
    # Insert dummy cluster and architectural pattern
    with get_connection() as conn:
        conn.execute("INSERT INTO root_cause_clusters (root_cause_category, total_occurrences, similarity_threshold, tool_names, pattern_ids) VALUES ('Network', 10, 0.60, '[\"tool1\", \"tool2\"]', '[1, 2]')")
        conn.execute("INSERT INTO architectural_patterns (cluster_id, pattern_name, description, affected_tools, evidence_count, status) VALUES (1, 'Test Pattern', 'Test Desc', '[\"tool1\", \"tool2\"]', 10, 'active')")
        
        # Insert a successful fix for context
        conn.execute("INSERT INTO improvement_history (cycle_id, improvement_type, tool_name, result, problem_description, fix_summary, weakness_category, baseline_fitness, candidate_fitness, fitness_delta) VALUES (1, 'tool', 'tool1', 'deployed', 'Failed due to timeout', 'Added retry logic', 'Network', 0.5, 0.9, 0.4)")
        # Actually find_successful_fixes expects weakness_category to be the root_cause_category?
        # Let's fix that. In DB it might not be. find_successful_fixes searches by category if provided, wait, I used `find_successful_fixes(None, None, root_cause_category=category)` which might use `weakness_category = ?` or something. Let me check later if needed. For now it's a test.
        conn.execute("UPDATE improvement_history SET weakness_category = 'Network'")
        
    from aria.rootcause.hypotheses import generate_hypotheses
    
    with patch("aria.rootcause.hypotheses.Groq") as MockGroq:
        mock_client = MagicMock()
        MockGroq.return_value = mock_client
        mock_resp = MagicMock()
        mock_resp.choices[0].message.content = json.dumps({
            "root_cause_summary": "Network instability",
            "proposed_fix_summary": "Add retries",
            "target_tools": ["tool1"],
            "confidence": 0.85
        })
        mock_client.chat.completions.create.return_value = mock_resp
        
        res = generate_hypotheses("aria.db")
        assert res["hypotheses_created"] == 1
        
        with get_connection() as conn:
            h = conn.execute("SELECT * FROM hypotheses WHERE id=1").fetchone()
            assert h["status"] == "proposed"
            assert h["confidence"] == 0.85
            assert "tool1" in h["target_tools"]
            
        # Test Deduplication
        res2 = generate_hypotheses("aria.db")
        assert res2["hypotheses_created"] == 0
        
    print("Hypothesis Generation passed.")

def test_select_next_target():
    print("Testing Select Next Target...")
    from aria.introspection.engine import IntrospectionEngine
    from aria.config import settings
    
    engine = IntrospectionEngine()
    engine.analyze_tool = MagicMock(return_value=MagicMock(tool_name="tool1", success_rate=0.5))
    engine.analyze_all = MagicMock(return_value=[MagicMock(tool_name="worst_tool")])
    
    # Should select the proposed hypothesis (confidence 0.85 > threshold)
    target = engine.select_next_target()
    assert target["mode"] == "hypothesis"
    assert target["hypothesis_id"] == 1
    assert target["report"].tool_name == "tool1"
    assert target["report"].hypothesis["confidence"] == 0.85
    
    # Change DB confidence to 0.50 to force fallback (threshold is 0.60)
    with get_connection() as conn:
        conn.execute("UPDATE hypotheses SET confidence = 0.50 WHERE id = 1")
        
    target2 = engine.select_next_target()
    assert target2["mode"] == "weakness"
    assert target2["report"].tool_name == "worst_tool"
    
    print("Select Next Target passed.")

def test_hypothesis_outcome():
    print("Testing Hypothesis Outcome logic...")
    from aria.rootcause.hypotheses import mark_hypothesis_outcome
    
    # Fail 3 times
    mark_hypothesis_outcome(1, None, False)
    with get_connection() as conn:
        assert tuple(conn.execute("SELECT attempt_count, status FROM hypotheses WHERE id=1").fetchone()) == (1, "proposed")
        
    mark_hypothesis_outcome(1, None, False)
    mark_hypothesis_outcome(1, None, False)
    with get_connection() as conn:
        assert tuple(conn.execute("SELECT attempt_count, status FROM hypotheses WHERE id=1").fetchone()) == (3, "rejected")
        
    print("Hypothesis Outcome passed.")
    
def test_prompt_injection():
    print("Testing Prompt Injection...")
    from aria.introspection.engine import WeaknessReport
    from aria.improvement.prompts import build_improvement_prompt
    
    report = WeaknessReport(
        tool_name="tool1",
        success_rate=0.5,
        p90_latency=1.0,
        total_executions=10,
        failure_count=5,
        fitness_score=0.5,
        reasons=[],
        recent_failures=[],
        source_code="print('hello')",
        hypothesis={
            "root_cause_summary": "Test root cause",
            "proposed_fix_summary": "Test proposed fix",
        }
    )
    
    prompt = build_improvement_prompt(report)
    assert "## DIRECTIVE (from Root Cause Analysis)" in prompt
    assert "Test root cause" in prompt
    assert "Test proposed fix" in prompt
    print("Prompt Injection passed.")

if __name__ == "__main__":
    reset_db()
    test_hypothesis_generation()
    test_select_next_target()
    test_hypothesis_outcome()
    test_prompt_injection()
    print("ALL TESTS PASSED!")
