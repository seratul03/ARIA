from __future__ import annotations

import json
import logging
from datetime import datetime

from aria.config import settings
from aria.metrics.db import get_connection
from aria.rootcause.categories import RootCauseCategory, CATEGORY_DESCRIPTIONS, HEURISTIC_RULES, HEURISTIC_THRESHOLD
from aria.core.rate_limiter import groq_limiter

logger = logging.getLogger(__name__)

def classify_pattern_heuristic(pattern: dict) -> tuple[RootCauseCategory, float] | None:
    text = f"{pattern.get('error_type', '')} {pattern.get('representative_error', '')}"
    for regex, category, conf in HEURISTIC_RULES:
        if regex.search(text):
            return category, conf
    return None

def classify_pattern_llm(pattern: dict, similar_examples: list[dict]) -> tuple[RootCauseCategory, float, str]:
    """
    Builds a prompt containing CATEGORY_DESCRIPTIONS and the pattern to classify.
    """
    try:
        from groq import Groq
        client = Groq(api_key=settings.groq_api_key)
    except ImportError:
        logger.error("[RootCauseClassifier] Groq package not installed.")
        return RootCauseCategory.LOGIC, 0.3, "llm_fallback"
        
    descriptions_json = json.dumps({c.value: desc for c, desc in CATEGORY_DESCRIPTIONS.items()}, indent=2)
    
    prompt = f"""You are ARIA's Root Cause Classification Engine. 
Assign exactly ONE category from the provided taxonomy to the following failure pattern.

TAXONOMY:
{descriptions_json}

PATTERN TO CLASSIFY:
Error Type: {pattern.get('error_type', 'Unknown')}
Error Message: {pattern.get('representative_error', 'Unknown')}
Occurrence Count: {pattern.get('occurrence_count', 0)}
Tools Affected: {pattern.get('tool_names', '[]')}

Stack Trace / Signature:
{pattern.get('traceback_signature', '')}

FEW-SHOT EXAMPLES:
{json.dumps(similar_examples, indent=2) if similar_examples else "None"}

Return STRICT JSON matching this schema:
{{
  "category": "<one of the taxonomy keys>",
  "confidence": <float between 0.0 and 1.0>,
  "justification": "<short string explaining the choice>"
}}
"""

    def call_llm() -> dict | None:
        groq_limiter.acquire()
        try:
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON-only expert classifier."},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                max_tokens=300,
                temperature=0.1,
            )
            raw = response.choices[0].message.content or "{}"
            return json.loads(raw)
        except Exception as e:
            logger.warning(f"[RootCauseClassifier] LLM call failed: {e}")
            return None

    # First attempt
    res = call_llm()
    
    # Validation & Retry
    for _ in range(2):
        if res and "category" in res and "confidence" in res and "justification" in res:
            try:
                # Try to map the string back to the enum
                category_str = res["category"]
                # In case LLM returns the enum value (e.g. "Validation" instead of "VALIDATION", though they are the same here)
                category = RootCauseCategory(category_str)
                confidence = float(res["confidence"])
                return category, confidence, "llm"
            except ValueError:
                pass # Invalid category string, retry
        
        # Retry
        res = call_llm()

    # Fallback
    logger.warning("[RootCauseClassifier] LLM failed to return valid JSON after retry. Using fallback.")
    return RootCauseCategory.LOGIC, 0.3, "llm_fallback"

def classify_unclassified_patterns(max_llm_calls: int = 10) -> dict:
    summary = {"heuristic": 0, "llm": 0, "skipped": 0, "fallback": 0}
    llm_calls_made = 0
    now_str = datetime.utcnow().isoformat() + "Z"

    with get_connection() as conn:
        # We need representative_error, error_type. They are in failure_history, linked by representative_failure_id
        rows = conn.execute("""
            SELECT fp.id, fp.traceback_signature, fp.tool_names, fp.occurrence_count, 
                   fh.error_type, fh.error_message as representative_error
            FROM failure_patterns fp
            JOIN failure_history fh ON fp.representative_failure_id = fh.id
            WHERE fp.root_cause_category IS NULL AND fp.status = 'active'
            ORDER BY fp.occurrence_count DESC
        """).fetchall()

        patterns_to_classify = [dict(r) for r in rows]
        
        for pattern in patterns_to_classify:
            assigned_category = None
            assigned_confidence = 0.0
            assigned_method = None
            
            # 1. Heuristic
            heur_res = classify_pattern_heuristic(pattern)
            if heur_res and heur_res[1] >= HEURISTIC_THRESHOLD:
                assigned_category, assigned_confidence = heur_res
                assigned_method = "heuristic"
                summary["heuristic"] += 1
            else:
                # 2. LLM
                if llm_calls_made < max_llm_calls:
                    # Get similar examples
                    examples_rows = conn.execute("""
                        SELECT fp.traceback_signature, fp.root_cause_category, fh.error_type, fh.error_message
                        FROM failure_patterns fp
                        JOIN failure_history fh ON fp.representative_failure_id = fh.id
                        WHERE fp.root_cause_category IS NOT NULL
                          AND fp.traceback_signature LIKE ?
                        LIMIT 2
                    """, (pattern["traceback_signature"][:5] + "%",)).fetchall()
                    examples = [dict(r) for r in examples_rows]
                    
                    assigned_category, assigned_confidence, assigned_method = classify_pattern_llm(pattern, examples)
                    
                    if assigned_method == "llm":
                        summary["llm"] += 1
                    else:
                        summary["fallback"] += 1
                        
                    llm_calls_made += 1
                else:
                    summary["skipped"] += 1
                    continue
            
            # Update failure_patterns
            conn.execute("""
                UPDATE failure_patterns 
                SET root_cause_category = ?, root_cause_confidence = ?, root_cause_method = ?, root_cause_assigned_at = ?
                WHERE id = ?
            """, (assigned_category.value, assigned_confidence, assigned_method, now_str, pattern["id"]))
            
            # Backfill improvement_history
            conn.execute("""
                UPDATE improvement_history SET weakness_category = ?
                WHERE triggering_failure_id IN (
                    SELECT id FROM failure_history WHERE traceback_signature = ?
                ) AND weakness_category IS NULL
            """, (assigned_category.value, pattern["traceback_signature"]))

    return summary
