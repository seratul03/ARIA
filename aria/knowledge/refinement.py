import json
import logging
import sqlite3
from typing import Dict, Any, List

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.tools.registry import registry
from aria.knowledge.confidence import initial_confidence, update_rule_status
from aria.knowledge.export import export_rules_json

logger = logging.getLogger(__name__)

REFINEMENT_CONFIDENCE_BAND = (0.40, 0.60)
MIN_APPLICATIONS_FOR_REFINEMENT = 5
REFINEMENT_IMPROVEMENT_MARGIN = 0.05

def identify_refinement_candidates(db_path: str) -> List[Dict[str, Any]]:
    candidates = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # status='active', apps >= MIN, confidence in band, scope IS NULL (or empty)
        query = f"""
            SELECT * FROM engineering_rules
            WHERE status = 'active'
            AND applications_count >= {MIN_APPLICATIONS_FOR_REFINEMENT}
            AND confidence >= ? AND confidence <= ?
            AND (scope IS NULL OR scope = '')
            ORDER BY applications_count DESC
        """
        rows = conn.execute(query, REFINEMENT_CONFIDENCE_BAND).fetchall()
        for r in rows:
            candidates.append(dict(r))
            
    return candidates

def gather_application_contexts(rule_id: int, db_path: str, limit_each: int = 5) -> Dict[str, List[Dict]]:
    contexts = {"successes": [], "failures": []}
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        for outcome in ["success", "failure"]:
            query = """
                SELECT ih.tool_name, ih.problem_description, ih.fix_summary, ih.weakness_category
                FROM rule_applications ra
                JOIN improvement_history ih ON ra.improvement_history_id = ih.id
                WHERE ra.rule_id = ? AND ra.outcome = ?
                ORDER BY ra.applied_at DESC
                LIMIT ?
            """
            rows = conn.execute(query, (rule_id, outcome, limit_each)).fetchall()
            for r in rows:
                key = "successes" if outcome == "success" else "failures"
                contexts[key].append(dict(r))
                
    return contexts

def refine_rule(rule: Dict[str, Any], contexts: Dict[str, List[Dict]], db_path: str) -> Dict[str, Any] | None:
    try:
        from groq import Groq
    except ImportError:
        logger.error("[Refinement] Groq not installed.")
        return None
        
    client = Groq(api_key=settings.groq_api_key)
    
    success_str = json.dumps(contexts.get("successes", []), indent=2)
    failure_str = json.dumps(contexts.get("failures", []), indent=2)
    
    prompt = f"""You are an expert engineer refining a generalized rule that has mixed outcomes.
The current rule works well in some contexts but fails in others. Your task is to identify the distinguishing factor between the successes and failures, and produce a narrower, refined rule.

Original Rule Text: {rule['rule_text']}
Category: {rule['category']}

SUCCESS CONTEXTS:
{success_str}

FAILURE CONTEXTS:
{failure_str}

If you cannot find a clear distinguishing factor, you must explicitly output "no distinguishing factor found" in the rationale and make the `refined_rule_text` identical to the original rule.
If you CAN find a distinguishing factor, return a refined rule text and a non-empty `scope`.

CRITICAL RULE: Your `refined_rule_text` MUST NOT contain the name of any specific tool.

Return EXACTLY this JSON structure:
{{
  "refined_rule_text": "...",
  "scope": "The specific context where this rule safely applies (e.g., Only for external network calls)",
  "rationale": "...",
  "confidence": 0.5
}}
"""

    known_tool_names = registry.names()
    
    for attempt in range(2):
        groq_limiter.acquire()
        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": "You are a senior engineer. Output valid JSON only."},
                    {"role": "user", "content": prompt}
                ],
                response_format={"type": "json_object"},
                max_tokens=600,
                temperature=0.3 + (attempt * 0.2)
            )
            
            raw = response.choices[0].message.content or "{}"
            result = json.loads(raw)
            
            refined_text = result.get("refined_rule_text", "")
            scope = result.get("scope", "")
            rationale = result.get("rationale", "")
            
            if "no distinguishing factor found" in rationale.lower() or refined_text == rule['rule_text']:
                logger.info(f"[Refinement] Rule {rule['id']} rejected: no distinguishing factor found.")
                return None
                
            if not scope:
                logger.info(f"[Refinement] Rule {rule['id']} rejected: empty scope.")
                return None
                
            contains_tool = any(tool_name in refined_text for tool_name in known_tool_names)
            if contains_tool:
                logger.warning(f"[Refinement] Validation failed: rule_text contains tool name on attempt {attempt+1}")
                prompt += "\n\nREJECTION: Your last output contained a specific tool name. You MUST generalize it."
                continue
                
            return {
                "refined_rule_text": refined_text,
                "scope": scope,
                "rationale": rationale,
                "confidence": float(result.get("confidence", 0.5))
            }
            
        except Exception as e:
            logger.error(f"[Refinement] LLM call failed: {e}")
            
    return None

def apply_refinements(db_path: str, max_llm_calls: int = 3) -> Dict[str, int]:
    candidates = identify_refinement_candidates(db_path)[:max_llm_calls]
    
    stats = {"refined": 0, "no_distinguishing_factor": 0}
    
    for candidate in candidates:
        contexts = gather_application_contexts(candidate['id'], db_path)
        
        # If we don't have enough data on both sides, skip
        if len(contexts['successes']) == 0 or len(contexts['failures']) == 0:
            stats["no_distinguishing_factor"] += 1
            continue
            
        refined = refine_rule(candidate, contexts, db_path)
        
        if not refined:
            stats["no_distinguishing_factor"] += 1
            continue
            
        # Overwrite source_type for initial_confidence to prevent durability_bonus
        fake_source = candidate.copy()
        fake_source["source_type"] = "refinement"
        init_conf = initial_confidence(fake_source, refined["confidence"])
        
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                INSERT INTO engineering_rules 
                (rule_text, category, scope, source_type, source_id, initial_confidence, confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate')
            """, (
                refined["refined_rule_text"],
                candidate["category"],
                refined["scope"],
                "refinement",
                candidate["id"],
                init_conf,
                init_conf
            ))
            
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            
            conn.execute("UPDATE engineering_rules SET status = 'superseded', superseded_by = ? WHERE id = ?", (new_id, candidate["id"]))
            
        stats["refined"] += 1
        
    if stats["refined"] > 0:
        export_rules_json(db_path)
        
    return stats

def evaluate_refinement_effectiveness(db_path: str) -> Dict[str, int]:
    stats = {"validated": 0, "reverted": 0, "still_pending": 0}
    
    MIN_APPS_FOR_EVAL = 5  # We could use MIN_APPLICATIONS_FOR_DEPRECATION from confidence.py
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # Find active refined rules with enough evidence
        refined_rules = conn.execute("""
            SELECT * FROM engineering_rules 
            WHERE source_type = 'refinement' 
            AND status = 'active' 
            AND applications_count >= ?
        """, (MIN_APPS_FOR_EVAL,)).fetchall()
        
        for r_row in refined_rules:
            refined = dict(r_row)
            original = conn.execute("SELECT * FROM engineering_rules WHERE id = ?", (refined["source_id"],)).fetchone()
            
            if not original:
                continue
                
            # Compare confidence
            if refined["confidence"] > original["confidence"] + REFINEMENT_IMPROVEMENT_MARGIN:
                stats["validated"] += 1
                # Status already active, validated implicitly
            elif refined["confidence"] <= original["confidence"]:
                # Revert
                conn.execute("UPDATE engineering_rules SET status = 'deprecated', deprecation_reason = 'refinement_ineffective' WHERE id = ?", (refined["id"],))
                # Re-activate original and un-supersede it
                conn.execute("UPDATE engineering_rules SET status = 'active', superseded_by = NULL WHERE id = ?", (original["id"],))
                stats["reverted"] += 1
            else:
                stats["still_pending"] += 1
                
        if stats["reverted"] > 0:
            export_rules_json(db_path)
            
    return stats
