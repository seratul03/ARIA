"""
aria/knowledge/extraction.py
──────────────────────────────
Extracts engineering rules from durable Phase 2 fixes (hypotheses and architectural_patterns).
"""

import json
import logging
import sqlite3
from typing import Dict, Any, List

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.tools.registry import registry
from aria.rootcause.categories import RootCauseCategory, CATEGORY_DESCRIPTIONS
from aria.knowledge.export import export_rules_json
from aria.knowledge.confidence import initial_confidence

logger = logging.getLogger(__name__)

def find_unconverted_sources(db_path: str, status_filter: str, min_evidence: int = 6) -> List[Dict[str, Any]]:
    """
    Returns sources from Phase 2 that have no existing engineering_rules row.
    If status_filter == 'resolved', finds durable fixes (Day 16).
    If status_filter == 'active', finds unresolved patterns with high evidence (Day 21).
    """
    sources = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        if status_filter == 'resolved':
            # Day 16: Architectural patterns
            arch_query = """
                SELECT id as source_id, 
                       pattern_name as root_cause_summary, 
                       affected_tools as target_tools,
                       'architectural_pattern' as source_type,
                       'Logic' as category
                FROM architectural_patterns
                WHERE status = 'resolved'
                AND NOT EXISTS (
                    SELECT 1 FROM engineering_rules 
                    WHERE source_type = 'architectural_pattern' AND source_id = architectural_patterns.id
                )
                ORDER BY id ASC
            """
            try:
                arch_rows = conn.execute(arch_query).fetchall()
                for r in arch_rows:
                    sources.append(dict(r))
            except sqlite3.OperationalError:
                pass

            # Day 16: Hypotheses
            hyp_query = """
                SELECT h.id as source_id,
                       h.root_cause_summary,
                       h.target_tools,
                       'hypothesis' as source_type,
                       COALESCE(c.root_cause_category, 'Logic') as category,
                       h.resolved_improvement_id
                FROM hypotheses h
                LEFT JOIN root_cause_clusters c ON h.source_id = c.id AND h.source_type = 'cluster'
                WHERE h.status = 'implemented'
                AND NOT EXISTS (
                    SELECT 1 FROM engineering_rules
                    WHERE source_type = 'hypothesis' AND source_id = h.id
                )
                ORDER BY h.id ASC
            """
            try:
                hyp_rows = conn.execute(hyp_query).fetchall()
                for r in hyp_rows:
                    imp_id = r["resolved_improvement_id"]
                    if not imp_id: continue
                    imp_row = conn.execute("SELECT tool_name, timestamp, fix_summary FROM improvement_history WHERE id = ?", (imp_id,)).fetchone()
                    if not imp_row: continue
                    tool_name = imp_row["tool_name"]
                    ts = imp_row["timestamp"]
                    rollback_count = conn.execute("SELECT COUNT(*) as c FROM improvement_history WHERE tool_name = ? AND result = 'rolled_back' AND timestamp >= ?", (tool_name, ts)).fetchone()["c"]
                    if rollback_count == 0:
                        row_dict = dict(r)
                        row_dict["fix_summary"] = imp_row["fix_summary"]
                        sources.append(row_dict)
            except sqlite3.OperationalError:
                pass

        elif status_filter == 'active':
            # Day 21: Architectural patterns
            arch_query = f"""
                SELECT id as source_id, 
                       pattern_name as root_cause_summary, 
                       affected_tools as target_tools,
                       'architectural_pattern' as source_type,
                       'Logic' as category
                FROM architectural_patterns
                WHERE status = 'active' AND evidence_count >= {min_evidence}
                AND NOT EXISTS (
                    SELECT 1 FROM engineering_rules 
                    WHERE source_type = 'architectural_pattern' AND source_id = architectural_patterns.id
                )
                ORDER BY id ASC
            """
            try:
                arch_rows = conn.execute(arch_query).fetchall()
                for r in arch_rows:
                    sources.append(dict(r))
            except sqlite3.OperationalError:
                pass

            # Day 21: Root cause clusters (single tool)
            cluster_query = f"""
                SELECT id as source_id,
                       cluster_summary as root_cause_summary,
                       target_tools,
                       'cluster' as source_type,
                       root_cause_category as category
                FROM root_cause_clusters
                WHERE status = 'active' AND total_occurrences >= {min_evidence}
                AND NOT EXISTS (
                    SELECT 1 FROM engineering_rules
                    WHERE source_type = 'cluster' AND source_id = root_cause_clusters.id
                )
                ORDER BY id ASC
            """
            try:
                cluster_rows = conn.execute(cluster_query).fetchall()
                for r in cluster_rows:
                    # check if single tool
                    tools = json.loads(r["target_tools"])
                    if len(tools) == 1:
                        sources.append(dict(r))
            except sqlite3.OperationalError:
                pass
                
    return sources



def extract_rule_from_source(source: Dict[str, Any], db_path: str) -> Dict[str, Any] | None:
    """
    Builds an LLM prompt to extract a general rule.
    Validates that the rule_text does NOT contain tool names.
    Retries once on validation failure.
    """
    try:
        from groq import Groq
    except ImportError:
        logger.error("[Extraction] Groq not installed.")
        return None
        
    client = Groq(api_key=settings.groq_api_key)
    
    root_cause = source.get("root_cause_summary", "")
    target_tools = source.get("target_tools", "[]")
    category = source.get("category", "Logic")
    
    # Try to get the Enum description if possible
    try:
        cat_enum = RootCauseCategory(category)
        cat_desc = CATEGORY_DESCRIPTIONS.get(cat_enum, "")
    except ValueError:
        cat_desc = ""
        
    fix_summary = source.get("fix_summary", source.get("proposed_fix_summary", "Unknown fix applied"))
    
    prompt = f"""You are a senior principal engineer analyzing a resolved failure in an autonomous system.
We want to extract a highly general, universally applicable engineering rule/principle from this fix.

Root Cause / Problem: {root_cause}
Category: {category} - {cat_desc}
Tools Originally Affected: {target_tools}
Actual Fix Implemented: {fix_summary}

Your task:
Extract a strict, generalized engineering principle from this.
CRITICAL RULE: Your `rule_text` MUST NOT contain the name of any specific tool (e.g. no "search_tool", "weather_tool"). It must apply to ANY component doing a similar job.

Return EXACTLY this JSON structure:
{{
  "rule_text": "The generalized principle...",
  "llm_confidence": 0.85,
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
                temperature=0.3 + (attempt * 0.2)
            )
            
            raw = response.choices[0].message.content or "{}"
            result = json.loads(raw)
            
            rule_text = result.get("rule_text", "")
            if not rule_text:
                continue
                
            contains_tool = any(tool_name in rule_text for tool_name in known_tool_names)
            if contains_tool:
                logger.warning(f"[Extraction] Validation failed: rule_text contains tool name on attempt {attempt+1}")
                prompt += "\n\nREJECTION: Your last output contained a specific tool name. You MUST generalize it."
                continue
                
            return {
                "rule_text": rule_text,
                "llm_confidence": float(result.get("llm_confidence", 0.5)),
                "applies_when": result.get("applies_when", "")
            }
            
        except Exception as e:
            logger.error(f"[Extraction] LLM call failed: {e}")
            
    return None

def extract_rules_from_durable_fixes(db_path: str, max_llm_calls: int = 5) -> Dict[str, Any]:
    sources = find_unconverted_sources(db_path, 'resolved')
    if not sources:
        return {"extracted": 0, "skipped": 0, "remaining": 0}
        
    extracted_count = 0
    skipped_count = 0
    
    to_process = sources[:max_llm_calls]
    remaining = len(sources) - len(to_process)
    
    for source in to_process:
        extracted = extract_rule_from_source(source, db_path)
        if not extracted:
            skipped_count += 1
            remaining += 1
            continue
            
        init_conf = initial_confidence(source, extracted["llm_confidence"])

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
