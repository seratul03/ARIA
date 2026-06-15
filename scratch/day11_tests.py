import os
import time
import sqlite3
import json
from pathlib import Path
from rapidfuzz import fuzz

from aria.metrics.db import init_db, get_connection
from aria.memory.schema import run_migrations

def reset_db():
    if os.path.exists("aria.db"):
        os.remove("aria.db")
    init_db("aria.db")
    run_migrations("aria.db")

def seed_day11_data():
    now = time.time()
    
    with get_connection() as conn:
        # We need to insert failure_history first, then failure_patterns referencing it.
        # P1: Timeout 1
        conn.execute("INSERT INTO failure_history (tool_name, source, error_message, traceback_signature) VALUES ('t1', 'audit', 'Connection timed out after 5s', 'sig_t1')")
        fh1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, representative_failure_id, tool_names, occurrence_count, root_cause_category, first_seen, last_seen, status)
            VALUES ('sig_t1', ?, '["t1"]', 10, 'Network', ?, ?, 'active')
        """, (fh1, now, now))
        p1 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # P2: Timeout 2 (similar to P1)
        conn.execute("INSERT INTO failure_history (tool_name, source, error_message, traceback_signature) VALUES ('t2', 'audit', 'Read timeout while connecting', 'sig_t2')")
        fh2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, representative_failure_id, tool_names, occurrence_count, root_cause_category, first_seen, last_seen, status)
            VALUES ('sig_t2', ?, '["t2"]', 5, 'Network', ?, ?, 'active')
        """, (fh2, now, now))
        p2 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # P3: Unrelated Network error (DNS)
        conn.execute("INSERT INTO failure_history (tool_name, source, error_message, traceback_signature) VALUES ('t3', 'audit', 'DNS resolution failed for api.example.com', 'sig_dns')")
        fh3 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, representative_failure_id, tool_names, occurrence_count, root_cause_category, first_seen, last_seen, status)
            VALUES ('sig_dns', ?, '["t3"]', 3, 'Network', ?, ?, 'active')
        """, (fh3, now, now))
        p3 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        # P4: Unrelated Network error (429)
        conn.execute("INSERT INTO failure_history (tool_name, source, error_message, traceback_signature) VALUES ('t4', 'audit', 'HTTP 429 Too Many Requests', 'sig_429')")
        fh4 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, representative_failure_id, tool_names, occurrence_count, root_cause_category, first_seen, last_seen, status)
            VALUES ('sig_429', ?, '["t4"]', 2, 'Network', ?, ?, 'active')
        """, (fh4, now, now))
        p4 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        return p1, p2, p3, p4

def test_similarity():
    print("Testing Similarity Threshold...")
    score1 = fuzz.token_set_ratio("Connection timed out after 5s", "Read timeout while connecting")
    score2 = fuzz.token_set_ratio("DNS resolution failed for api.example.com", "HTTP 429 Too Many Requests")
    assert score1 >= 60, f"Score1 is {score1}, expected >= 60"
    assert score2 < 60, f"Score2 is {score2}, expected < 60"
    print("Similarity Test passed.")

def test_clustering_creation():
    print("Testing Initial Clustering...")
    from aria.rootcause.clustering import cluster_all_categories
    stats = cluster_all_categories("aria.db")
    
    assert stats["clusters_created"] == 1
    assert stats["clusters_updated"] == 0
    
    # We should have exactly 1 cluster combining P1 and P2
    with get_connection() as conn:
        clusters = conn.execute("SELECT * FROM root_cause_clusters").fetchall()
        assert len(clusters) == 1
        
        c = clusters[0]
        p_ids = json.loads(c["pattern_ids"])
        assert len(p_ids) == 2
        assert c["total_occurrences"] == 15
        
        tools = json.loads(c["tool_names"])
        assert sorted(tools) == ["t1", "t2"]
        
    print("Clustering Creation passed.")

def test_idempotency():
    print("Testing Idempotency...")
    from aria.rootcause.clustering import cluster_all_categories
    stats = cluster_all_categories("aria.db")
    
    # Existing cluster updated, no new created
    assert stats["clusters_created"] == 0
    assert stats["clusters_updated"] == 1
    
    with get_connection() as conn:
        clusters = conn.execute("SELECT * FROM root_cause_clusters").fetchall()
        assert len(clusters) == 1
    print("Idempotency passed.")

def test_cluster_growth():
    print("Testing Cluster Growth...")
    now = time.time()
    with get_connection() as conn:
        conn.execute("INSERT INTO failure_history (tool_name, source, error_message, traceback_signature) VALUES ('t5', 'audit', 'Connection read timeout', 'sig_t5')")
        fh5 = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        
        conn.execute("""
            INSERT INTO failure_patterns (traceback_signature, representative_failure_id, tool_names, occurrence_count, root_cause_category, first_seen, last_seen, status)
            VALUES ('sig_t5', ?, '["t5"]', 8, 'Network', ?, ?, 'active')
        """, (fh5, now, now))
        
    from aria.rootcause.clustering import cluster_all_categories
    stats = cluster_all_categories("aria.db")
    
    # Should update the existing cluster, not create a new one
    assert stats["clusters_created"] == 0
    assert stats["clusters_updated"] == 1
    
    with get_connection() as conn:
        clusters = conn.execute("SELECT * FROM root_cause_clusters").fetchall()
        assert len(clusters) == 1
        c = clusters[0]
        p_ids = json.loads(c["pattern_ids"])
        assert len(p_ids) == 3
        assert c["total_occurrences"] == 23
        tools = json.loads(c["tool_names"])
        assert sorted(tools) == ["t1", "t2", "t5"]
        
    print("Cluster Growth passed.")

if __name__ == "__main__":
    reset_db()
    seed_day11_data()
    test_similarity()
    test_clustering_creation()
    test_idempotency()
    test_cluster_growth()
    print("ALL TESTS PASSED!")
