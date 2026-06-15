import json
import logging
from typing import Dict, Any

from aria.metrics.db import get_connection
from aria.core.rate_limiter import groq_limiter
from aria.config import settings
from groq import Groq

logger = logging.getLogger(__name__)

MIN_EVIDENCE = 4

PROMPT_TEMPLATE = """You are an expert software architect analyzing recurring failures across multiple tools.
You are given a cluster of related failures that span multiple tools.

Category: {category}
Category Description: {category_desc}
Affected Tools: {tools}

Representative Error Messages in this cluster:
{errors}

Your task is to identify the MISSING ENGINEERING CAPABILITY or ARCHITECTURAL FLAW that is causing these failures.
Do not just describe the symptom (e.g., "Network timeout"). Describe the missing capability (e.g., "Missing Retry Logic for Network Calls").

Respond ONLY with a valid JSON object matching this schema:
{{
    "pattern_name": "<A short, descriptive name for the architectural pattern, max 80 chars>",
    "description": "<A 1-2 sentence explanation of the underlying flaw and why it affects these tools>"
}}
"""

def extract_architectural_patterns(db_path: str) -> dict:
    from aria.rootcause.categories import CATEGORY_DESCRIPTIONS
    stats = {"patterns_created": 0, "patterns_updated": 0, "patterns_resolved": 0}
    
    with get_connection() as conn:
        clusters = conn.execute("SELECT * FROM root_cause_clusters WHERE total_occurrences >= ?", (MIN_EVIDENCE,)).fetchall()
        
    for cluster in clusters:
        try:
            tools = json.loads(cluster["tool_names"])
            p_ids = json.loads(cluster["pattern_ids"])
        except Exception:
            continue
            
        if len(tools) < 2:
            continue # Must be cross-tool
            
        cluster_id = cluster["id"]
        category = cluster["root_cause_category"]
        total_occurrences = cluster["total_occurrences"]
        
        with get_connection() as conn:
            existing = conn.execute("SELECT * FROM architectural_patterns WHERE cluster_id = ?", (cluster_id,)).fetchone()
            
            # Get statuses of all underlying patterns to see if resolved
            # We also get error messages if we need to call LLM
            placeholders = ",".join("?" * len(p_ids))
            query = f"""
                SELECT p.status, h.error_message 
                FROM failure_patterns p
                JOIN failure_history h ON p.representative_failure_id = h.id
                WHERE p.id IN ({placeholders})
            """
            pattern_rows = conn.execute(query, p_ids).fetchall()
            
            all_resolved = len(pattern_rows) > 0 and all(r["status"] == "resolved" for r in pattern_rows)
            error_msgs = list(set([r["error_message"] for r in pattern_rows if r["error_message"]]))
            
            if existing:
                # Update existing
                updated = False
                if existing["status"] == "active" and all_resolved:
                    conn.execute("UPDATE architectural_patterns SET status = 'resolved', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (existing["id"],))
                    stats["patterns_resolved"] += 1
                    updated = True
                
                # Update evidence count / affected tools
                old_evidence = existing["evidence_count"]
                try:
                    old_tools = json.loads(existing["affected_tools"])
                except:
                    old_tools = []
                
                if total_occurrences > old_evidence or len(tools) > len(old_tools):
                    conn.execute("""
                        UPDATE architectural_patterns 
                        SET evidence_count = ?, affected_tools = ?, updated_at = CURRENT_TIMESTAMP 
                        WHERE id = ?
                    """, (total_occurrences, json.dumps(tools), existing["id"]))
                    if not updated:
                        stats["patterns_updated"] += 1
            else:
                # Create new
                pattern_name, description = _generate_pattern_with_llm(
                    category, tools, error_msgs, CATEGORY_DESCRIPTIONS.get(category, "")
                )
                
                conn.execute("""
                    INSERT INTO architectural_patterns 
                    (cluster_id, pattern_name, description, affected_tools, evidence_count, status)
                    VALUES (?, ?, ?, ?, ?, 'active')
                """, (cluster_id, pattern_name, description, json.dumps(tools), total_occurrences))
                
                # Backfill cluster label
                conn.execute("UPDATE root_cause_clusters SET cluster_label = ? WHERE id = ?", (pattern_name, cluster_id))
                
                stats["patterns_created"] += 1
                
    return stats


def _generate_pattern_with_llm(category: str, tools: list[str], error_msgs: list[str], cat_desc: str) -> tuple[str, str]:
    fallback_name = f"{category} issue affecting {', '.join(tools)}"
    fallback_desc = f"Multiple tools ({', '.join(tools)}) are experiencing related {category} failures."
    
    prompt = PROMPT_TEMPLATE.format(
        category=category,
        category_desc=cat_desc,
        tools=", ".join(tools),
        errors="\n".join([f"- {e}" for e in error_msgs[:5]]) # limit to 5
    )
    
    try:
        client = Groq(api_key=settings.groq_api_key)
    except Exception as e:
        logger.warning(f"Failed to initialize Groq client: {e}")
        return fallback_name, fallback_desc
        
    for attempt in range(2):
        try:
            groq_limiter.acquire()
            resp = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300,
                response_format={"type": "json_object"}
            )
            
            content = resp.choices[0].message.content
            data = json.loads(content)
            
            name = data.get("pattern_name", "")
            desc = data.get("description", "")
            
            if not name or not desc:
                raise ValueError("Missing name or description")
                
            if len(name) > 80:
                raise ValueError("Name too long")
                
            # Basic sanity check for raw stack trace
            lower_name = name.lower()
            if "traceback" in lower_name or "file " in lower_name or "line " in lower_name:
                raise ValueError("Name contains stack trace artifacts")
                
            return name, desc
            
        except Exception as e:
            logger.warning(f"LLM pattern generation failed attempt {attempt+1}: {e}")
            
    return fallback_name, fallback_desc
