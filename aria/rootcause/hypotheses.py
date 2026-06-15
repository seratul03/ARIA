import json
import logging
from typing import Dict, Any, List

from groq import Groq

from aria.config import settings
from aria.metrics.db import get_connection
from aria.core.rate_limiter import groq_limiter
from aria.rootcause.categories import CATEGORY_DESCRIPTIONS
from aria.memory.retrieval import find_successful_fixes

logger = logging.getLogger(__name__)

def generate_hypotheses(db_path: str = None) -> Dict[str, int]:
    """
    Generate actionable hypotheses from active architectural patterns.
    """
    if db_path is None:
        db_path = str(settings.db_path)
        
    hypotheses_created = 0
    client = Groq(api_key=settings.groq_api_key)
    
    with get_connection() as conn:
        # Source A: architectural_patterns WHERE status='active' AND no active hypothesis exists
        query = """
            SELECT ap.*, rcc.root_cause_category
            FROM architectural_patterns ap
            JOIN root_cause_clusters rcc ON ap.cluster_id = rcc.id
            WHERE ap.status = 'active'
              AND NOT EXISTS (
                  SELECT 1 FROM hypotheses h 
                  WHERE h.source_type = 'architectural_pattern' 
                    AND h.source_id = ap.id
                    AND h.status IN ('proposed', 'accepted', 'implemented')
              )
        """
        patterns = [dict(row) for row in conn.execute(query).fetchall()]
        
    for p in patterns:
        category = p["root_cause_category"]
        desc = CATEGORY_DESCRIPTIONS.get(category, "Unknown category.")
        
        # Retrieve past fixes
        past_fixes = find_successful_fixes(None, None, root_cause_category=category, top_k=3)
        past_fixes_text = ""
        if past_fixes:
            past_fixes_text = "PREVIOUSLY SUCCESSFUL FIXES IN THIS CATEGORY:\n" + "\n".join(
                [f"- {fix['fix_summary']}" for fix in past_fixes]
            )
            
        affected_tools = json.loads(p["affected_tools"])
        tool_names_str = ", ".join(affected_tools)
        
        prompt = f"""You are an expert system architect analyzing a cross-cutting failure pattern in a fleet of tools.

Pattern Name: {p['pattern_name']}
Pattern Description: {p['description']}
Affected Tools: {tool_names_str}
Category Context: {desc}

{past_fixes_text}

Provide an actionable hypothesis to fix this systemic issue.
You MUST output strictly valid JSON, with exactly these four keys:
- "root_cause_summary": A concise 1-sentence summary of the underlying root cause.
- "proposed_fix_summary": A concrete, actionable fix to implement in the tools.
- "target_tools": A JSON array of tool names (strings) that should be prioritized for this fix. Usually a subset of Affected Tools.
- "confidence": A float between 0.0 and 1.0 indicating how likely this fix is to resolve the pattern.

Return ONLY the JSON. No markdown formatting, no explanations.
"""
        
        fallback_used = False
        for attempt in range(2):
            try:
                groq_limiter.acquire()
                resp = client.chat.completions.create(
                    model=settings.groq_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=256,
                )
                raw = resp.choices[0].message.content.strip()
                if raw.startswith("```json"):
                    raw = raw[7:-3].strip()
                elif raw.startswith("```"):
                    raw = raw[3:-3].strip()
                
                data = json.loads(raw)
                root_cause = data["root_cause_summary"]
                proposed_fix = data["proposed_fix_summary"]
                target_tools = data["target_tools"]
                confidence = float(data["confidence"])
                
                if not isinstance(target_tools, list) or len(target_tools) == 0:
                    raise ValueError("target_tools must be a non-empty list")
                
                # Success!
                break
            except Exception as e:
                logger.warning(f"[Hypotheses] LLM generation failed attempt {attempt+1}: {e}")
                if attempt == 1:
                    fallback_used = True
                    
        if fallback_used:
            root_cause = f"Systemic weakness matching {p['pattern_name']} across multiple tools."
            proposed_fix = f"Implement robustness mechanisms handling {category} issues."
            target_tools = affected_tools
            confidence = 0.5
            
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO hypotheses (source_type, source_id, root_cause_summary, proposed_fix_summary, target_tools, confidence)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ("architectural_pattern", p["id"], root_cause, proposed_fix, json.dumps(target_tools), confidence))
            hypotheses_created += 1
            
    return {"hypotheses_created": hypotheses_created}

def mark_hypothesis_outcome(hypothesis_id: int, improvement_history_id: int | None, deployed: bool) -> None:
    """
    Called after an improvement cycle targeting this hypothesis concludes.
    """
    with get_connection() as conn:
        if deployed:
            conn.execute("""
                UPDATE hypotheses 
                SET status = 'implemented', resolved_improvement_id = ? 
                WHERE id = ?
            """, (improvement_history_id, hypothesis_id))
        else:
            # Increment attempt count
            conn.execute("UPDATE hypotheses SET attempt_count = attempt_count + 1 WHERE id = ?", (hypothesis_id,))
            row = conn.execute("SELECT attempt_count FROM hypotheses WHERE id = ?".format(), (hypothesis_id,)).fetchone()
            if row and row["attempt_count"] >= 3:
                conn.execute("UPDATE hypotheses SET status = 'rejected' WHERE id = ?", (hypothesis_id,))
