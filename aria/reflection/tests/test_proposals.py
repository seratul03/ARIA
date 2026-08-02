import pytest
import sqlite3
import json
import os
import tempfile
from aria.reflection.proposals import (
    generate_proposals_from_weaknesses,
    generate_proposals_from_mistakes,
    generate_proposals_from_complex_findings,
    evaluate_implemented_proposals
)

@pytest.fixture
def db_path(tmp_path):
    db_file = tmp_path / "test_aria.db"
    with sqlite3.connect(db_file) as conn:
        conn.executescript("""
        CREATE TABLE architectural_weaknesses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weakness_type TEXT,
            severity TEXT,
            description TEXT,
            evidence_json TEXT,
            status TEXT
        );
        
        CREATE TABLE recurring_mistakes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mistake_type TEXT,
            description TEXT,
            evidence_json TEXT,
            status TEXT
        );
        
        CREATE TABLE ineffective_improvements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            target_component TEXT,
            description TEXT,
            evidence_json TEXT,
            status TEXT
        );
        
        CREATE TABLE token_waste_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            waste_type TEXT,
            description TEXT,
            evidence_json TEXT,
            estimated_tokens_wasted_per_cycle INTEGER,
            status TEXT
        );
        
        CREATE TABLE bad_prompt_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            prompt_type TEXT,
            finding_type TEXT,
            description TEXT,
            evidence_json TEXT,
            correlation_metric REAL,
            status TEXT
        );
        
        CREATE TABLE self_improvement_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            source_finding_type TEXT,
            source_finding_id INTEGER,
            proposal_text TEXT,
            target_module TEXT,
            change_type TEXT,
            success_metric TEXT,
            measurement_window_cycles INTEGER,
            priority TEXT,
            status TEXT DEFAULT 'proposed',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            accepted_at DATETIME,
            implemented_at DATETIME,
            implementation_notes TEXT,
            evaluation_at DATETIME,
            outcome TEXT,
            outcome_notes TEXT,
            snapshot_id INTEGER
        );
        """)
    return str(db_file)

def test_generate_proposals_weaknesses(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO architectural_weaknesses (weakness_type, severity, description, evidence_json, status)
            VALUES ('category_blind_spot', 'high', 'desc', ?, 'active')
        """, (json.dumps({"category": "Syntax"}),))
        conn.commit()

    count1 = generate_proposals_from_weaknesses(db_path, snapshot_id=1)
    assert count1 == 1

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        props = conn.execute("SELECT * FROM self_improvement_proposals").fetchall()
        assert len(props) == 1
        p = props[0]
        assert "Syntax" in p["title"]
        assert p["target_module"] == "aria/rootcause/hypotheses.py"
        assert p["priority"] == "critical"

    count2 = generate_proposals_from_weaknesses(db_path, snapshot_id=1)
    assert count2 == 0

    with sqlite3.connect(db_path) as conn:
        count_db = conn.execute("SELECT COUNT(*) FROM self_improvement_proposals").fetchone()[0]
        assert count_db == 1

def test_generate_proposals_mistakes(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO recurring_mistakes (mistake_type, description, evidence_json, status)
            VALUES ('rule_violation_pattern', 'desc', ?, 'active')
        """, (json.dumps({"rule_id": "123"}),))
        conn.commit()

    count1 = generate_proposals_from_mistakes(db_path, snapshot_id=1)
    assert count1 == 1

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        props = conn.execute("SELECT * FROM self_improvement_proposals WHERE source_finding_type='mistake'").fetchall()
        assert len(props) == 1
        p = props[0]
        assert "123" in p["title"]
        assert p["target_module"] == "aria/improvement/prompt_builder.py"
        assert p["priority"] == "high"

def test_generate_proposals_complex_findings(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO ineffective_improvements (strategy, target_component, description, evidence_json, status)
            VALUES ('s', 't', 'desc', ?, 'active')
        """, (json.dumps({
            "mock_target_module": "aria/reflection/proposals.py",
            "mock_metric": "Metric > 10"
        }),))
        
        conn.execute("""
            INSERT INTO bad_prompt_findings (prompt_type, finding_type, description, evidence_json, correlation_metric, status)
            VALUES ('p', 'f', 'desc', ?, 0.1, 'active')
        """, (json.dumps({
            "mock_target_module": "aria/cli.py",
            "mock_metric": "Metric drops by 15 within 10 cycles"
        }),))
        conn.commit()

    count = generate_proposals_from_complex_findings(db_path, snapshot_id=1)
    assert count == 1 

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        props = conn.execute("SELECT * FROM self_improvement_proposals WHERE source_finding_type IN ('bad_prompt', 'ineffective', 'token_waste')").fetchall()
        assert len(props) == 1
        assert props[0]["target_module"] == "aria/cli.py"

def test_evaluate_implemented_proposals(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("""
            INSERT INTO self_improvement_proposals 
            (title, source_finding_type, source_finding_id, proposal_text, target_module, change_type, success_metric, measurement_window_cycles, priority, status, evaluation_at, snapshot_id)
            VALUES 
            ('Success Prop', 'w', 1, 'text', 'mod', 'type', 'SUCCESS_TEST', 10, 'high', 'implemented', CURRENT_TIMESTAMP, 1),
            ('Fail Prop', 'w', 2, 'text', 'mod', 'type', 'FAIL_TEST', 10, 'high', 'implemented', CURRENT_TIMESTAMP, 1),
            ('Future Prop', 'w', 3, 'text', 'mod', 'type', 'SUCCESS_TEST', 10, 'high', 'implemented', datetime('now', '+1 day'), 1)
        """)
        conn.commit()

    stats = evaluate_implemented_proposals(db_path)
    assert stats["evaluated"] == 2
    assert stats["success"] == 1
    assert stats["failure"] == 1

    with sqlite3.connect(db_path) as conn:
        success_prop = conn.execute("SELECT outcome FROM self_improvement_proposals WHERE title='Success Prop'").fetchone()[0]
        fail_prop = conn.execute("SELECT outcome FROM self_improvement_proposals WHERE title='Fail Prop'").fetchone()[0]
        future_prop = conn.execute("SELECT outcome FROM self_improvement_proposals WHERE title='Future Prop'").fetchone()[0]

        assert success_prop == 'success'
        assert fail_prop == 'failure'
        assert future_prop is None

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as td:
        db = os.path.join(td, "test_aria.db")
        with sqlite3.connect(db) as conn:
            conn.executescript("""
            CREATE TABLE architectural_weaknesses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                weakness_type TEXT,
                severity TEXT,
                description TEXT,
                evidence_json TEXT,
                status TEXT
            );
            
            CREATE TABLE recurring_mistakes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mistake_type TEXT,
                description TEXT,
                evidence_json TEXT,
                status TEXT
            );
            
            CREATE TABLE ineffective_improvements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT,
                target_component TEXT,
                description TEXT,
                evidence_json TEXT,
                status TEXT
            );
            
            CREATE TABLE token_waste_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                waste_type TEXT,
                description TEXT,
                evidence_json TEXT,
                estimated_tokens_wasted_per_cycle INTEGER,
                status TEXT
            );
            
            CREATE TABLE bad_prompt_findings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prompt_type TEXT,
                finding_type TEXT,
                description TEXT,
                evidence_json TEXT,
                correlation_metric REAL,
                status TEXT
            );
            
            CREATE TABLE self_improvement_proposals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                source_finding_type TEXT,
                source_finding_id INTEGER,
                proposal_text TEXT,
                target_module TEXT,
                change_type TEXT,
                success_metric TEXT,
                measurement_window_cycles INTEGER,
                priority TEXT,
                status TEXT DEFAULT 'proposed',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                accepted_at DATETIME,
                implemented_at DATETIME,
                implementation_notes TEXT,
                evaluation_at DATETIME,
                outcome TEXT,
                outcome_notes TEXT,
                snapshot_id INTEGER
            );
            """)
        print("Testing weaknesses...")
        test_generate_proposals_weaknesses(db)
        print("Testing mistakes...")
        test_generate_proposals_mistakes(db)
        print("Testing complex findings...")
        test_generate_proposals_complex_findings(db)
        print("Testing evaluation...")
        test_evaluate_implemented_proposals(db)
        print("All tests passed.")
