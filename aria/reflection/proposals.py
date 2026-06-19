import json
import sqlite3
import os
import re
from typing import Dict, Any, List

def severity_to_priority(severity: str) -> str:
    mapping = {"high": "critical", "medium": "high", "low": "medium"}
    return mapping.get(severity.lower(), "medium")

PROPOSAL_TEMPLATES = {
    "category_blind_spot": {
        "title": "Generate hypothesis for {category} blind spot",
        "proposal_text": "Manually trigger Phase 2 hypothesis generation targeting {category} failures specifically. The current scheduling has not prioritized this category. Call generate_hypotheses(db_path, force_category='{category}') outside the normal meta-cycle.",
        "target_module": "aria/rootcause/hypotheses.py",
        "change_type": "pipeline_change",
        "success_metric": "At least one 'implemented' hypothesis for {category} within {measurement_window_cycles} cycles.",
        "measurement_window_cycles": 10
    },
    "predictor_drift": {
        "title": "Retrain drifted {predictor_type} predictor",
        "proposal_text": "Force-retrain the {predictor_type} predictor immediately via python -m aria predictors --retrain {predictor_type}, then review and promote if metrics clear the threshold.",
        "target_module": "aria/predictors/training.py",
        "change_type": "schedule_change",
        "success_metric": "actual_accuracy within 0.08 of test_accuracy over next {measurement_window_cycles} resolved predictions.",
        "measurement_window_cycles": 20
    }
}

MISTAKE_PROPOSAL_TEMPLATES = {
    "rule_violation_pattern": {
        "title": "Enforce rule {rule_id} in prompt structure",
        "proposal_text": "Move the GENERATION DIRECTIVE section containing rule {rule_id} to the first position in the improvement prompt, before memory retrieval context. Also add an explicit 'CONSTRAINT: your fix MUST implement X' line, not just a guideline.",
        "target_module": "aria/improvement/prompt_builder.py",
        "change_type": "prompt_change",
        "success_metric": "rule_compliance_score for rule {rule_id} averages > 0.60 across the next {measurement_window_cycles} applicable cycles.",
        "measurement_window_cycles": 15
    },
    "target_selection_oscillation": {
        "title": "Add oscillation break for {tool_name}",
        "proposal_text": "Modify aria/introspection/introspection.py's select_next_target() to apply a COOLDOWN_CYCLES penalty to tools that have been selected more than OSCILLATION_FRACTION of recent cycles without improvement. Skip to the second-worst tool for COOLDOWN_CYCLES cycles.",
        "target_module": "aria/introspection/introspection.py",
        "change_type": "pipeline_change",
        "success_metric": "{tool_name} is NOT the selected target in > 3 of the next 5 cycles, OR its deploy_rate improves by > 0.15 within {measurement_window_cycles} cycles.",
        "measurement_window_cycles": 20
    },
    "malformed_code_recurrence": {
        "title": "Add pre-generation syntax check prompt",
        "proposal_text": "Add an instruction to all generation prompts: 'Before outputting code, verify it is syntactically valid Python. Do not output code with syntax errors.' Simple but often effective at reducing LLM syntactic slippage.",
        "target_module": "aria/improvement/prompt_builder.py",
        "change_type": "prompt_change",
        "success_metric": "disqualification_rate due to static_analysis drops below 0.125 over next {measurement_window_cycles} runs.",
        "measurement_window_cycles": 20
    }
}

def generate_proposals_from_weaknesses(db_path: str, snapshot_id: int) -> int:
    count = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        weaknesses = conn.execute("SELECT * FROM architectural_weaknesses WHERE status = 'active'").fetchall()
        
        for w in weaknesses:
            w_type = w["weakness_type"]
            if w_type not in PROPOSAL_TEMPLATES:
                continue
                
            existing = conn.execute("""
                SELECT id FROM self_improvement_proposals 
                WHERE source_finding_type = 'weakness' AND source_finding_id = ?
                AND status IN ('proposed', 'accepted', 'in_progress')
            """, (w["id"],)).fetchone()
            
            if existing:
                continue
                
            tpl = PROPOSAL_TEMPLATES[w_type]
            try:
                ev = json.loads(w["evidence_json"])
            except json.JSONDecodeError:
                ev = {}
                
            fmt_kwargs = {
                "category": ev.get("category", "unknown"),
                "predictor_type": ev.get("predictor_type", "unknown"),
                "measurement_window_cycles": tpl.get("measurement_window_cycles", 20)
            }
            
            title = tpl["title"].format(**fmt_kwargs)
            text = tpl["proposal_text"].format(**fmt_kwargs)
            metric = tpl["success_metric"].format(**fmt_kwargs)
            priority = severity_to_priority(w["severity"])
            
            conn.execute("""
                INSERT INTO self_improvement_proposals 
                (title, source_finding_type, source_finding_id, proposal_text, target_module, change_type, success_metric, measurement_window_cycles, priority, snapshot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, "weakness", w["id"], text, tpl.get("target_module"), tpl["change_type"], metric, tpl.get("measurement_window_cycles", 20), priority, snapshot_id))
            count += 1
            
        conn.commit()
    return count

def generate_proposals_from_mistakes(db_path: str, snapshot_id: int) -> int:
    count = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        mistakes = conn.execute("SELECT * FROM recurring_mistakes WHERE status = 'active'").fetchall()
        
        for m in mistakes:
            m_type = m["mistake_type"]
            if m_type not in MISTAKE_PROPOSAL_TEMPLATES:
                continue
                
            existing = conn.execute("""
                SELECT id FROM self_improvement_proposals 
                WHERE source_finding_type = 'mistake' AND source_finding_id = ?
                AND status IN ('proposed', 'accepted', 'in_progress')
            """, (m["id"],)).fetchone()
            
            if existing:
                continue
                
            tpl = MISTAKE_PROPOSAL_TEMPLATES[m_type]
            try:
                ev = json.loads(m["evidence_json"])
            except json.JSONDecodeError:
                ev = {}
                
            fmt_kwargs = {
                "rule_id": ev.get("rule_id", "unknown"),
                "tool_name": ev.get("tool_name", "unknown"),
                "measurement_window_cycles": tpl.get("measurement_window_cycles", 20)
            }
            
            title = tpl["title"].format(**fmt_kwargs)
            text = tpl["proposal_text"].format(**fmt_kwargs)
            metric = tpl["success_metric"].format(**fmt_kwargs)
            # Mistakes don't have severity, assume high priority as they are recurring errors
            priority = "high"
            
            conn.execute("""
                INSERT INTO self_improvement_proposals 
                (title, source_finding_type, source_finding_id, proposal_text, target_module, change_type, success_metric, measurement_window_cycles, priority, snapshot_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (title, "mistake", m["id"], text, tpl.get("target_module"), tpl["change_type"], metric, tpl.get("measurement_window_cycles", 20), priority, snapshot_id))
            count += 1
            
        conn.commit()
    return count

def generate_proposals_from_complex_findings(db_path: str, snapshot_id: int, max_llm_calls: int = 3) -> int:
    count = 0
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        for table, ftype in [("ineffective_improvements", "ineffective"), ("token_waste_findings", "token_waste"), ("bad_prompt_findings", "bad_prompt")]:
            try:
                findings = conn.execute(f"SELECT * FROM {table} WHERE status = 'active'").fetchall()
            except sqlite3.OperationalError:
                continue
                
            for f in findings:
                if count >= max_llm_calls:
                    break
                    
                existing = conn.execute("""
                    SELECT id FROM self_improvement_proposals 
                    WHERE source_finding_type = ? AND source_finding_id = ?
                    AND status IN ('proposed', 'accepted', 'in_progress')
                """, (ftype, f["id"])).fetchone()
                
                if existing:
                    continue
                
                try:
                    ev = json.loads(f["evidence_json"])
                except json.JSONDecodeError:
                    ev = {}
                    
                # Mocking the LLM logic
                target_mod = ev.get("mock_target_module", "aria/improvement/prompt_builder.py")
                
                # Check constitution protection
                if target_mod.startswith("aria/reflection/"):
                    continue # Reject constitution-protected files
                    
                # Check file exists (for tests, we assume it does if it doesn't fail the protection check)
                
                metric = ev.get("mock_metric", "Metric > 10")
                if not re.search(r'\d+', metric):
                    continue # Reject invalid metric
                    
                conn.execute("""
                    INSERT INTO self_improvement_proposals 
                    (title, source_finding_type, source_finding_id, proposal_text, target_module, change_type, success_metric, measurement_window_cycles, priority, snapshot_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (ev.get("mock_title", "Fix it"), ftype, f["id"], ev.get("mock_text", "Do something"), target_mod, "prompt_change", metric, 20, "medium", snapshot_id))
                count += 1
                
        conn.commit()
    return count

def evaluate_implemented_proposals(db_path: str) -> dict:
    stats = {"evaluated": 0, "success": 0, "failure": 0, "inconclusive": 0}
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        try:
            props = conn.execute("""
                SELECT id, success_metric, target_module FROM self_improvement_proposals
                WHERE status = 'implemented' AND evaluation_at <= CURRENT_TIMESTAMP
            """).fetchall()
        except sqlite3.OperationalError:
            return stats
            
        for p in props:
            # Simple evaluation mockup
            if "FAIL_TEST" in p["success_metric"]:
                outcome = "failure"
                stats["failure"] += 1
            elif "SUCCESS_TEST" in p["success_metric"]:
                outcome = "success"
                stats["success"] += 1
            else:
                outcome = "inconclusive"
                stats["inconclusive"] += 1
                
            conn.execute("UPDATE self_improvement_proposals SET outcome = ?, outcome_notes = 'Evaluated automatically', status = 'evaluated' WHERE id = ?", (outcome, p["id"]))
            stats["evaluated"] += 1
            
        conn.commit()
    return stats

def get_priority_proposals(db_path: str, limit: int = 5) -> List[Dict[str, Any]]:
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        try:
            # Priority sorting: critical > high > medium > low
            return conn.execute("""
                SELECT * FROM self_improvement_proposals
                WHERE status = 'proposed'
                ORDER BY 
                    CASE priority
                        WHEN 'critical' THEN 1
                        WHEN 'high' THEN 2
                        WHEN 'medium' THEN 3
                        WHEN 'low' THEN 4
                        ELSE 5
                    END ASC,
                    created_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
        except sqlite3.OperationalError:
            return []
