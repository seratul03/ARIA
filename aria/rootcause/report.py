import json
from typing import Any
from aria.config import settings
from aria.metrics.db import get_connection
from aria.rootcause.statistics import root_cause_breakdown
from aria.core.rate_limiter import groq_limiter

def generate_root_cause_report(db_path: str | None = None, llm_narrative: bool = True) -> dict[str, Any]:
    """
    Assembles a comprehensive root cause report synthesizing breakdown, clusters, 
    architectural patterns, hypotheses, and fix durability.
    If llm_narrative=True, makes ONE Groq LLM call to write a plain-English narrative.
    """
    if db_path is None:
        db_path = str(settings.db_path)
        
    breakdown = root_cause_breakdown(weight_by="occurrence_count")
    
    with get_connection() as conn:
        top_clusters_rows = conn.execute(
            "SELECT * FROM root_cause_clusters ORDER BY total_occurrences DESC LIMIT 5"
        ).fetchall()
        top_clusters = [dict(r) for r in top_clusters_rows]
        for c in top_clusters:
            # Parse json strings for cleaner output
            c["tool_names"] = json.loads(c["tool_names"]) if isinstance(c["tool_names"], str) else c["tool_names"]
            c["pattern_ids"] = json.loads(c["pattern_ids"]) if isinstance(c["pattern_ids"], str) else c["pattern_ids"]
        
        architectural_patterns_rows = conn.execute(
            "SELECT * FROM architectural_patterns WHERE status = 'active' ORDER BY evidence_count DESC LIMIT 5"
        ).fetchall()
        architectural_patterns = [dict(r) for r in architectural_patterns_rows]
        for p in architectural_patterns:
            p["affected_tools"] = json.loads(p["affected_tools"]) if isinstance(p["affected_tools"], str) else p["affected_tools"]
        
        hypotheses_proposed = [dict(r) for r in conn.execute(
            "SELECT * FROM hypotheses WHERE status = 'proposed' ORDER BY confidence DESC LIMIT 5"
        ).fetchall()]
        for h in hypotheses_proposed:
            h["target_tools"] = json.loads(h["target_tools"]) if isinstance(h["target_tools"], str) else h["target_tools"]
            
        hypotheses_implemented = [dict(r) for r in conn.execute(
            "SELECT * FROM hypotheses WHERE status = 'implemented' AND created_at >= datetime('now', '-30 days') ORDER BY created_at DESC LIMIT 5"
        ).fetchall()]
        for h in hypotheses_implemented:
            h["target_tools"] = json.loads(h["target_tools"]) if isinstance(h["target_tools"], str) else h["target_tools"]
            
        held = 0
        rolled_back = 0
        
        # Check all implemented hypotheses for fix durability, not just the top 5
        all_implemented = conn.execute(
            "SELECT resolved_improvement_id FROM hypotheses WHERE status = 'implemented'"
        ).fetchall()
        
        for row in all_implemented:
            resolved_id = row["resolved_improvement_id"]
            if not resolved_id:
                continue
                
            imp = conn.execute("SELECT tool_name, timestamp FROM improvement_history WHERE id = ?", (resolved_id,)).fetchone()
            if not imp:
                continue
                
            # Check for a subsequent rollback in improvement history for this tool
            rollback_check = conn.execute(
                "SELECT 1 FROM improvement_history WHERE tool_name = ? AND result = 'rolled_back' AND timestamp > ?",
                (imp["tool_name"], imp["timestamp"])
            ).fetchone()
            
            if rollback_check:
                rolled_back += 1
            else:
                held += 1

    structured_data = {
        "root_cause_breakdown": breakdown,
        "top_clusters": top_clusters,
        "architectural_patterns": architectural_patterns,
        "hypotheses": {"proposed": hypotheses_proposed, "implemented_recent": hypotheses_implemented},
        "fix_durability": {"held": held, "rolled_back": rolled_back},
        "narrative": None
    }
    
    if llm_narrative:
        try:
            from groq import Groq
            client = Groq(api_key=settings.groq_api_key)
            
            prompt = (
                "You are ARIA. Provide a human-readable summary answering the question: "
                "'Why am I failing and what am I doing about it?'\n"
                "Write a 2-3 paragraph plain-English narrative based EXCLUSIVELY on the following structured data. "
                "Do NOT hallucinate tools, percentages, or fixes that are not present in this JSON.\n\n"
                f"{json.dumps(structured_data, default=str)}"
            )
            
            groq_limiter.acquire()
            resp = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=600,
            )
            structured_data["narrative"] = resp.choices[0].message.content.strip()
            
        except Exception as e:
            structured_data["narrative"] = f"Narrative generation failed: {e}"

    return structured_data
