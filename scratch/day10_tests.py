import os
import json
import time
import math
from aria.metrics.db import init_db, get_connection

# Re-init db in memory for clean test
if os.path.exists("aria.db"):
    os.remove("aria.db")
init_db("aria.db")
from aria.memory.schema import run_migrations
run_migrations("aria.db")

def setup_db():
    now = time.time()
    # 5 days = 432000 seconds
    
    with get_connection() as conn:
        # Insert 3 patterns: 2 network, 1 logic
        
        # P1: Network. Occurs 10 times in search_tool
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, status, representative_failure_id, occurrence_count, tool_names, root_cause_category, root_cause_method, first_seen, last_seen)
            VALUES ('sig_p1', 'active', 1, 10, '["search_tool"]', 'Network', 'heuristic', ?, ?)
        """, (now, now))
        
        # P2: Network. Occurs 5 times in weather_tool AND search_tool
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, status, representative_failure_id, occurrence_count, tool_names, root_cause_category, root_cause_method, first_seen, last_seen)
            VALUES ('sig_p2', 'active', 2, 5, '["search_tool", "weather_tool"]', 'Network', 'heuristic', ?, ?)
        """, (now, now))
        
        # P3: Logic. Occurs 5 times in calculator_tool
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, status, representative_failure_id, occurrence_count, tool_names, root_cause_category, root_cause_method, first_seen, last_seen)
            VALUES ('sig_p3', 'active', 3, 5, '["calculator_tool"]', 'Logic', 'llm', ?, ?)
        """, (now, now))
        
        # Seed failure history for trend testing
        # 30 days = 2592000 seconds. 
        # P1 failed 10 days ago (2x) and 5 days ago (8x)
        for _ in range(2):
            conn.execute("INSERT INTO failure_history (tool_name, error_type, error_message, traceback_signature, timestamp, input_snapshot, source) VALUES ('search_tool', 'err', 'msg', 'sig_p1', ?, '{}', 'audit')", (now - 864000,))
        for _ in range(8):
            conn.execute("INSERT INTO failure_history (tool_name, error_type, error_message, traceback_signature, timestamp, input_snapshot, source) VALUES ('search_tool', 'err', 'msg', 'sig_p1', ?, '{}', 'audit')", (now - 432000,))
            
        # P2 failed 20 days ago (5x)
        for _ in range(5):
            conn.execute("INSERT INTO failure_history (tool_name, error_type, error_message, traceback_signature, timestamp, input_snapshot, source) VALUES ('weather_tool', 'err', 'msg', 'sig_p2', ?, '{}', 'audit')", (now - 1728000,))
            
        # P3 failed yesterday (5x)
        for _ in range(5):
            conn.execute("INSERT INTO failure_history (tool_name, error_type, error_message, traceback_signature, timestamp, input_snapshot, source) VALUES ('calculator_tool', 'err', 'msg', 'sig_p3', ?, '{}', 'audit')", (now - 86400,))

def test_breakdown_fractions():
    print("Testing Breakdown Fractions...")
    from aria.rootcause.statistics import root_cause_breakdown
    
    # By occurrence:
    # Network = 10 + 5 = 15
    # Logic = 5
    # Total = 20
    occ = root_cause_breakdown(weight_by="occurrence_count")
    assert math.isclose(occ["Network"], 15/20)
    assert math.isclose(occ["Logic"], 5/20)
    assert math.isclose(sum(occ.values()), 1.0)
    
    # By pattern count:
    # Network = 2
    # Logic = 1
    # Total = 3
    pat = root_cause_breakdown(weight_by="pattern_count")
    assert math.isclose(pat["Network"], 2/3)
    assert math.isclose(pat["Logic"], 1/3)
    assert math.isclose(sum(pat.values()), 1.0)
    
    print("Breakdown Fractions passed.")


def test_tool_attribution():
    print("Testing Tool Attribution...")
    from aria.rootcause.statistics import root_cause_breakdown_by_tool
    
    breakdown = root_cause_breakdown_by_tool()
    
    # search_tool has P1 (10, Network) and P2 (5, Network) -> 15 Network (1.0)
    assert math.isclose(breakdown["search_tool"]["Network"], 1.0)
    
    # weather_tool has P2 (5, Network) -> 5 Network (1.0)
    assert math.isclose(breakdown["weather_tool"]["Network"], 1.0)
    
    # calculator_tool has P3 (5, Logic) -> 5 Logic (1.0)
    assert math.isclose(breakdown["calculator_tool"]["Logic"], 1.0)
    
    print("Tool Attribution passed.")


def test_timestamp_bucketing():
    print("Testing Timestamp Bucketing...")
    from aria.rootcause.statistics import root_cause_trend
    
    trend = root_cause_trend(window_days=30)
    # Expected buckets: 6 buckets of 5 days each.
    # Total buckets = 6. 
    # P1 (Network): bucket for 10 days ago (2x), bucket for 5 days ago (8x)
    # P2 (Network): bucket for 20 days ago (5x)
    # P3 (Logic): bucket for 1 day ago (5x)
    
    assert "Network" in trend
    assert "Logic" in trend
    assert len(trend["Network"]) == 6
    assert len(trend["Logic"]) == 6
    
    print("Timestamp Bucketing passed.")


def test_meta_introspection():
    print("Testing Meta Introspection Payload...")
    from aria.introspection.meta import run_meta_introspection
    
    # We need to mock settings to not call LLM and clone_manager to not run tests
    import aria.config
    import aria.core.rate_limiter
    
    run_meta_introspection(n_cycles=0)
    
    # Check self_model.json
    with open("self_model.json", "r") as f:
        sm = json.load(f)
        
    assert "root_cause_summary" in sm["memory_summary"]
    rcs = sm["memory_summary"]["root_cause_summary"]
    assert "by_occurrence" in rcs
    assert "by_pattern_count" in rcs
    assert "trend_30d" in rcs
    assert len(rcs["trend_30d"].get("Network", [])) == 6
    
    print("Meta Introspection Payload passed.")


if __name__ == "__main__":
    setup_db()
    test_breakdown_fractions()
    test_tool_attribution()
    test_timestamp_bucketing()
    test_meta_introspection()
    print("ALL TESTS PASSED!")
