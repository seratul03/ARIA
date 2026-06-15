import sqlite3
from typing import Dict
from aria.knowledge.confidence import update_rule_status
from aria.knowledge.export import export_rules_json

STALE_CANDIDATE_DAYS = 30

def prune_rules(db_path: str) -> Dict[str, int]:
    """
    1. For every status='active' rule with applications_count >= MIN_APPLICATIONS_FOR_DEPRECATION
       and confidence <= RULE_DEPRECATION_THRESHOLD: safety net scan
    2. STALE CANDIDATES: status='candidate' AND applications_count == 0
       AND created_at older than STALE_CANDIDATE_DAYS
    3. RE-EMERGENCE CHECK: for any rule just deprecated, check whether
       the *originating* pattern/hypothesis has since been re-activated
    """
    stats = {
        "deprecated_low_confidence": 0,
        "deprecated_stale": 0,
        "deprecated_regressed": 0
    }
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # 1. Safety net for 'active' rules
        active_rules = conn.execute("SELECT id FROM engineering_rules WHERE status = 'active'").fetchall()
        for r in active_rules:
            old_status = conn.execute("SELECT status FROM engineering_rules WHERE id = ?", (r["id"],)).fetchone()["status"]
            new_status = update_rule_status(r["id"], db_path)
            if old_status == 'active' and new_status == 'deprecated':
                stats["deprecated_low_confidence"] += 1
                
        # 2. Stale candidates
        stale_query = f"SELECT id FROM engineering_rules WHERE status = 'candidate' AND applications_count = 0 AND created_at <= datetime('now', '-{STALE_CANDIDATE_DAYS} days')"
        stale_candidates = conn.execute(stale_query).fetchall()
        
        for r in stale_candidates:
            conn.execute(
                "UPDATE engineering_rules SET status = 'deprecated', deprecation_reason = 'stale_candidate' WHERE id = ?",
                (r["id"],)
            )
            stats["deprecated_stale"] += 1
            
        # 3. Re-emergence check
        deprecated_rules = conn.execute(
            """
            SELECT id, source_type, source_id, deprecation_reason 
            FROM engineering_rules 
            WHERE status = 'deprecated' AND (deprecation_reason IS NULL OR deprecation_reason != 'source_regressed')
            """
        ).fetchall()
        
        for r in deprecated_rules:
            src_type = r["source_type"]
            src_id = r["source_id"]
            
            is_regressed = False
            # Hypotheses and architectural patterns have a 'status' field.
            # If the source is 'active', it means it regressed (was resolved/implemented, but became active again).
            try:
                if src_type == "architectural_pattern":
                    src = conn.execute("SELECT status FROM architectural_patterns WHERE id = ?", (src_id,)).fetchone()
                    if src and src["status"] == "active":
                        is_regressed = True
                elif src_type == "hypothesis":
                    src = conn.execute("SELECT status FROM hypotheses WHERE id = ?", (src_id,)).fetchone()
                    if src and src["status"] == "active":
                        is_regressed = True
            except sqlite3.OperationalError:
                # If the tables don't exist in testing, ignore safely.
                pass
                    
            if is_regressed:
                conn.execute(
                    "UPDATE engineering_rules SET deprecation_reason = 'source_regressed' WHERE id = ?",
                    (r["id"],)
                )
                stats["deprecated_regressed"] += 1
                
    export_rules_json(db_path)
    return stats
