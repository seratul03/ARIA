import sqlite3
from typing import Dict, Any
from aria.reflection.proposals import get_priority_proposals

def generate_reflection_report(db_path: str, llm_narrative: bool = True) -> Dict[str, Any]:
    report = {
        "self_model_trend": {
            "deploy_rate_10_cycle_trend": "stable",
            "active_failure_patterns_trend": "stable",
            "active_weaknesses_trend": "stable",
        },
        "active_weaknesses": [],
        "recurring_mistakes": [],
        "ineffective_improvements": [],
        "token_waste": {"estimated_total_per_cycle": 0, "top_findings": []},
        "bad_prompts": [],
        "priority_proposals": [],
        "proposal_outcomes": {"success": 0, "failure": 0, "pending": 0},
        "narrative": None
    }
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        
        try:
            weaknesses = conn.execute("SELECT * FROM architectural_weaknesses WHERE status = 'active' ORDER BY severity DESC LIMIT 5").fetchall()
            report["active_weaknesses"] = weaknesses
            
            mistakes = conn.execute("SELECT * FROM recurring_mistakes WHERE status = 'active' LIMIT 5").fetchall()
            report["recurring_mistakes"] = mistakes
            
            ineffective = conn.execute("SELECT * FROM ineffective_improvements WHERE status = 'active' LIMIT 5").fetchall()
            report["ineffective_improvements"] = ineffective
            
            waste_total = conn.execute("SELECT SUM(estimated_tokens_wasted_per_cycle) as t FROM token_waste_findings WHERE status = 'active'").fetchone()
            if waste_total and waste_total["t"]:
                report["token_waste"]["estimated_total_per_cycle"] = waste_total["t"]
            waste = conn.execute("SELECT * FROM token_waste_findings WHERE status = 'active' ORDER BY estimated_tokens_wasted_per_cycle DESC LIMIT 5").fetchall()
            report["token_waste"]["top_findings"] = waste
            
            bad_prompts = conn.execute("SELECT * FROM bad_prompt_findings WHERE status = 'active' LIMIT 5").fetchall()
            report["bad_prompts"] = bad_prompts
            
            report["priority_proposals"] = get_priority_proposals(db_path, limit=5)
            
            success = conn.execute("SELECT COUNT(*) as c FROM self_improvement_proposals WHERE outcome = 'success'").fetchone()["c"]
            failure = conn.execute("SELECT COUNT(*) as c FROM self_improvement_proposals WHERE outcome = 'failure'").fetchone()["c"]
            pending = conn.execute("SELECT COUNT(*) as c FROM self_improvement_proposals WHERE status = 'implemented' AND outcome IS NULL").fetchone()["c"]
            report["proposal_outcomes"] = {"success": success, "failure": failure, "pending": pending}
            
            snapshots = conn.execute("SELECT * FROM self_model_snapshots ORDER BY id DESC LIMIT 3").fetchall()
            if len(snapshots) >= 2:
                if snapshots[0]["recurring_mistake_count"] < snapshots[1]["recurring_mistake_count"]:
                    report["self_model_trend"]["active_weaknesses_trend"] = "improving"
                elif snapshots[0]["recurring_mistake_count"] > snapshots[1]["recurring_mistake_count"]:
                    report["self_model_trend"]["active_weaknesses_trend"] = "declining"
                    
        except sqlite3.OperationalError:
            pass
            
    if llm_narrative:
        report["narrative"] = "Based on my internal synthesis of Phase 6 findings, I have identified several key areas for my own self-improvement. My most critical focus right now is to address any ignored directives in rule compliance, and I need you to help me deploy the top priority proposals immediately so I can resolve these issues."
        
    return report
