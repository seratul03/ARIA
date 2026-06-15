import json
import logging
import sqlite3
from typing import Dict, Any

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.tools.registry import registry
from aria.rootcause.categories import RootCauseCategory, CATEGORY_DESCRIPTIONS
from aria.knowledge.extraction import find_unconverted_sources
from aria.knowledge.export import export_rules_json
from aria.knowledge.confidence import initial_confidence

logger = logging.getLogger(__name__)

PROACTIVE_MIN_EVIDENCE = 6

def find_proactive_sources(db_path: str) -> list[dict]:
    return find_unconverted_sources(db_path, 'active', min_evidence=PROACTIVE_MIN_EVIDENCE)

def generate_proactive_rule(source: Dict[str, Any], db_path: str) -> Dict[str, Any] | None:
    """
    Builds an LLM prompt to extract a proactive general rule from an unresolved pattern.
    """
    try:
        from groq import Groq
    except ImportError:
        logger.error("[Generation] Groq not installed.")
        return None
        
    client = Groq(api_key=settings.groq_api_key)
    
    root_cause = source.get("root_cause_summary", "")
    target_tools = source.get("target_tools", "[]")
    category = source.get("category", "Logic")
    
    try:
        cat_enum = RootCauseCategory(category)
        cat_desc = CATEGORY_DESCRIPTIONS.get(cat_enum, "")
    except ValueError:
        cat_desc = ""
        
    prompt = f"""You are a senior principal engineer analyzing a recurring failure pattern in an autonomous system.
We have NOT yet found a fix. Based on the pattern of failures below, what general engineering principle, if followed, would likely have prevented this class of issue?

Root Cause / Problem: {root_cause}
Category: {category} - {cat_desc}
Tools Affected: {target_tools}

Your task:
Extract a strict, generalized proactive engineering principle from this.
CRITICAL RULE: Your `rule_text` MUST NOT contain the name of any specific tool (e.g. no "search_tool", "weather_tool"). It must apply to ANY component doing a similar job.

Return EXACTLY this JSON structure:
{{
  "rule_text": "The generalized principle...",
  "llm_confidence": 0.40,
  "applies_when": "When this rule should be applied (e.g. outbound HTTP calls)"
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
                max_tokens=500,
                temperature=0.4 + (attempt * 0.2)
            )
            
            raw = response.choices[0].message.content or "{}"
            result = json.loads(raw)
            
            rule_text = result.get("rule_text", "")
            if not rule_text:
                continue
                
            contains_tool = any(tool_name in rule_text for tool_name in known_tool_names)
            if contains_tool:
                logger.warning(f"[Generation] Validation failed: rule_text contains tool name on attempt {attempt+1}")
                prompt += "\n\nREJECTION: Your last output contained a specific tool name. You MUST generalize it."
                continue
                
            return {
                "rule_text": rule_text,
                "llm_confidence": float(result.get("llm_confidence", 0.4)),
                "applies_when": result.get("applies_when", "")
            }
            
        except Exception as e:
            logger.error(f"[Generation] LLM call failed: {e}")
            
    return None

def generate_proactive_rules(db_path: str, max_llm_calls: int = 5) -> Dict[str, Any]:
    sources = find_proactive_sources(db_path)
    if not sources:
        return {"extracted": 0, "skipped": 0, "remaining": 0}
        
    extracted_count = 0
    skipped_count = 0
    
    to_process = sources[:max_llm_calls]
    remaining = len(sources) - len(to_process)
    
    for source in to_process:
        extracted = generate_proactive_rule(source, db_path)
        if not extracted:
            skipped_count += 1
            remaining += 1
            continue
            
        # Overwrite source_type for initial_confidence so it doesn't get durability bonus
        fake_source = source.copy()
        fake_source["source_type"] = "proactive_hypothesis" # Ensure no architectural_pattern bonus
        init_conf = initial_confidence(fake_source, extracted["llm_confidence"])

        with sqlite3.connect(db_path) as conn:
            cat = source.get("category", "Logic")
                
            conn.execute("""
                INSERT INTO engineering_rules 
                (rule_text, category, scope, source_type, source_id, initial_confidence, confidence, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate')
            """, (
                extracted["rule_text"],
                cat,
                extracted["applies_when"],
                source["source_type"],
                source["source_id"],
                init_conf,
                init_conf
            ))
            
        extracted_count += 1
        
    if extracted_count > 0:
        export_rules_json(db_path)
        
    return {
        "extracted": extracted_count,
        "skipped": skipped_count,
        "remaining": remaining
    }
