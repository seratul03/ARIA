import sqlite3
from typing import List, Dict
from aria.knowledge.confidence import recompute_confidence, update_rule_status

def select_rules_for_prompt(category: str | None, db_path: str, top_n: int = 3) -> List[Dict]:
    """
    Select active rules for prompt injection.
    """
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        if category:
            query = """
                SELECT * FROM engineering_rules 
                WHERE status = 'active' AND category = ?
                ORDER BY confidence DESC, applications_count DESC
                LIMIT ?
            """
            rows = conn.execute(query, (category, top_n)).fetchall()
            return [dict(r) for r in rows]
        else:
            query = """
                SELECT * FROM (
                    SELECT *,
                           ROW_NUMBER() OVER (PARTITION BY category ORDER BY confidence DESC) as rn
                    FROM engineering_rules
                    WHERE status = 'active'
                )
                WHERE rn = 1
                ORDER BY confidence DESC
                LIMIT ?
            """
            rows = conn.execute(query, (top_n,)).fetchall()
            return [dict(r) for r in rows]

def log_rule_applications(rule_ids: List[int], cycle_id: str | None, db_path: str) -> List[int]:
    """
    Log pending applications for the selected rules.
    Returns the list of rule_application ids.
    """
    app_ids = []
    with sqlite3.connect(db_path) as conn:
        for rule_id in rule_ids:
            cur = conn.execute(
                """
                INSERT INTO rule_applications (rule_id, cycle_id, outcome)
                VALUES (?, ?, 'pending')
                """,
                (rule_id, cycle_id)
            )
            app_ids.append(cur.lastrowid)
    return app_ids

def resolve_rule_applications_by_cycle(cycle_id: str, improvement_history_id: int | None, outcome: str, db_path: str):
    """Used for resolving pending rules when we know the cycle_id (e.g. from review_queue)"""
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT id FROM rule_applications WHERE cycle_id = ? AND outcome = 'pending'", (cycle_id,)).fetchall()
        app_ids = [r[0] for r in rows]
    resolve_rule_applications(app_ids, improvement_history_id, outcome, db_path)

def resolve_rule_applications(app_ids: List[int], improvement_history_id: int | None, outcome: str, db_path: str):
    """
    outcome must be 'success' or 'failure'.
    Updates the applications, the engineering_rules counts, and recomputes confidence.
    """
    with sqlite3.connect(db_path) as conn:
        for app_id in app_ids:
            conn.execute(
                """
                UPDATE rule_applications 
                SET outcome = ?, improvement_history_id = ? 
                WHERE id = ?
                """,
                (outcome, improvement_history_id, app_id)
            )
            
            row = conn.execute("SELECT rule_id FROM rule_applications WHERE id = ?", (app_id,)).fetchone()
            if not row: continue
            rule_id = row[0]
            
            success_inc = 1 if outcome == 'success' else 0
            conn.execute(
                """
                UPDATE engineering_rules 
                SET applications_count = applications_count + 1,
                    success_count = success_count + ?
                WHERE id = ?
                """,
                (success_inc, rule_id)
            )
            
    # Need to do confidence recalculation after committing updates so connection doesn't lock
    for app_id in app_ids:
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT rule_id FROM rule_applications WHERE id = ?", (app_id,)).fetchone()
        if row:
            rule_id = row[0]
            recompute_confidence(rule_id, db_path)
            update_rule_status(rule_id, db_path)
