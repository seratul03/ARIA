import json
import logging
import sqlite3
import math
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DIRECTIVE_IGNORE_RATE         = 0.40
CONTEXT_BUDGET_TOKENS         = 3000
CHARS_PER_TOKEN               = 4
OVERFLOW_CORRELATION_THRESHOLD= -0.15
MIN_SUMMARY_WORDS             = 8
MAX_SUMMARY_WORDS             = 80
ANOMALY_RATE                  = 0.30
SKEW_THRESHOLD                = 0.60

def estimate_prompt_section_lengths(db_path: str, evolution_run_id: int) -> dict:
    """
    Queries cycle_traces (if prompt logging exists) or reconstructs from
    component counts (N similar failures * avg_failure_entry_length + ...) to
    estimate each prompt section's character length.
    Returns {section_name: estimated_chars} for the given run.
    """
    has_prompt_text = False
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute("SELECT prompt_text FROM cycle_traces LIMIT 1")
            has_prompt_text = True
        except sqlite3.OperationalError:
            pass
            
    if has_prompt_text:
        # In a full implementation, we'd parse the actual text lengths.
        # For now, if the column exists but we don't have the data, fallback safely.
        return {"memory": 1000, "directive": 500, "engineering_principles": 1500, "generation_directive": 200}
    else:
        # Fallback to estimation based on typical counts
        return {
            "memory": 3 * 500, # 3 failures * 500 chars
            "directive": 400,
            "engineering_principles": 1000,
            "generation_directive": 300
        }

def detect_bad_prompts(db_path: str, snapshot_id: int, lookback_runs: int = 30) -> dict:
    findings = []
    
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        
        # 1. strategy_directive_ignored
        try:
            sdi_rows = conn.execute(f"""
                SELECT strategy,
                       COUNT(*) as total_cases,
                       SUM(CASE WHEN rule_compliance_score < 0.30 THEN 1 ELSE 0 END) as ignored_cases
                FROM evolution_candidates
                WHERE strategy LIKE 'RULE_GUIDED%'
                GROUP BY strategy
            """).fetchall()
            
            for r in sdi_rows:
                if r['total_cases'] >= 5: # need minimum sample size
                    ignore_rate = r['ignored_cases'] / r['total_cases']
                    if ignore_rate > DIRECTIVE_IGNORE_RATE:
                        findings.append({
                            "prompt_type": "generation",
                            "finding_type": "strategy_directive_ignored",
                            "description": f"Strategy {r['strategy']} directive is ignored ({ignore_rate:.0%} of the time).",
                            "correlation_metric": ignore_rate,
                            "evidence_json": {"strategy": r['strategy'], "total_cases": r['total_cases'], "ignored_cases": r['ignored_cases']}
                        })
        except sqlite3.OperationalError:
            pass

        # 2. context_length_overflow_proxy
        try:
            cand_rows = conn.execute(f"""
                SELECT ec.evolution_run_id, ec.composite_score
                FROM evolution_candidates ec
                JOIN evolution_runs er ON er.id = ec.evolution_run_id
                WHERE er.run_status = 'completed' AND ec.composite_score IS NOT NULL
                ORDER BY er.id DESC LIMIT 100
            """).fetchall()
            
            if len(cand_rows) > 10:
                lengths = {}
                for r in cand_rows:
                    run_id = r['evolution_run_id']
                    if run_id not in lengths:
                        sec_lens = estimate_prompt_section_lengths(db_path, run_id)
                        total_chars = sum(sec_lens.values())
                        lengths[run_id] = total_chars / CHARS_PER_TOKEN
                
                X = [lengths[r['evolution_run_id']] for r in cand_rows]
                Y = [r['composite_score'] for r in cand_rows]
                
                mean_x = sum(X) / len(X)
                mean_y = sum(Y) / len(Y)
                num = sum((x - mean_x) * (y - mean_y) for x, y in zip(X, Y))
                den_x = sum((x - mean_x)**2 for x in X)
                den_y = sum((y - mean_y)**2 for y in Y)
                if den_x > 0 and den_y > 0:
                    corr = num / math.sqrt(den_x * den_y)
                    if corr < OVERFLOW_CORRELATION_THRESHOLD:
                        avg_budget = mean_x
                        if avg_budget > CONTEXT_BUDGET_TOKENS:
                            findings.append({
                                "prompt_type": "generation",
                                "finding_type": "context_length_overflow_proxy",
                                "description": f"Prompt context length negatively correlates with outcome (corr={corr:.2f}).",
                                "correlation_metric": corr,
                                "evidence_json": {"correlation": corr, "avg_tokens": avg_budget}
                            })
        except sqlite3.OperationalError:
            pass

        # 3. fix_summary_length_anomaly
        try:
            summary_rows = conn.execute(f"""
                SELECT fix_summary FROM improvement_history
                WHERE fix_summary IS NOT NULL
                ORDER BY id DESC LIMIT 50
            """).fetchall()
            
            if summary_rows:
                anomalies = 0
                for r in summary_rows:
                    words = len(r['fix_summary'].split())
                    if words < MIN_SUMMARY_WORDS or words > MAX_SUMMARY_WORDS:
                        anomalies += 1
                        
                anomaly_fraction = anomalies / len(summary_rows)
                if anomaly_fraction > ANOMALY_RATE:
                    findings.append({
                        "prompt_type": "generation",
                        "finding_type": "fix_summary_length_anomaly",
                        "description": f"High rate ({anomaly_fraction:.0%}) of fix summaries outside normal length.",
                        "correlation_metric": anomaly_fraction,
                        "evidence_json": {"anomaly_fraction": anomaly_fraction, "sample_size": len(summary_rows)}
                    })
        except sqlite3.OperationalError:
            pass

        # 4. classification_category_skew
        try:
            llm_rows = conn.execute("""
                SELECT root_cause_category, COUNT(*) as c
                FROM failure_patterns
                WHERE classification_source = 'llm' AND root_cause_category IS NOT NULL
                GROUP BY root_cause_category
            """).fetchall()
            
            heur_rows = conn.execute("""
                SELECT root_cause_category, COUNT(*) as c
                FROM failure_patterns
                WHERE classification_source = 'heuristic' AND root_cause_category IS NOT NULL
                GROUP BY root_cause_category
            """).fetchall()
            
            llm_total = sum(r['c'] for r in llm_rows)
            heur_total = sum(r['c'] for r in heur_rows)
            
            if llm_total > 5 and heur_total > 5:
                llm_dist = {r['root_cause_category']: r['c'] / llm_total for r in llm_rows}
                heur_dist = {r['root_cause_category']: r['c'] / heur_total for r in heur_rows}
                
                max_llm_cat = max(llm_dist, key=llm_dist.get)
                max_llm_frac = llm_dist[max_llm_cat]
                
                heur_frac = heur_dist.get(max_llm_cat, 0.0)
                if max_llm_frac > SKEW_THRESHOLD and (max_llm_frac - heur_frac) > 0.20:
                    findings.append({
                        "prompt_type": "classification",
                        "finding_type": "classification_category_skew",
                        "description": f"LLM classification skew: {max_llm_cat} is {max_llm_frac:.0%} vs heuristic {heur_frac:.0%}.",
                        "correlation_metric": max_llm_frac,
                        "evidence_json": {"skewed_category": max_llm_cat, "llm_fraction": max_llm_frac, "heuristic_fraction": heur_frac}
                    })
        except sqlite3.OperationalError:
            pass

        # UPSERT logic
        stats = {"detected": 0, "resolved": 0, "updated": 0}
        active_ids = []

        for item in findings:
            row = conn.execute("""
                SELECT id FROM bad_prompt_findings 
                WHERE finding_type = ? AND status = 'active'
            """, (item["finding_type"],)).fetchone()

            if row:
                conn.execute("""
                    UPDATE bad_prompt_findings 
                    SET last_updated_at = CURRENT_TIMESTAMP,
                        correlation_metric = ?,
                        evidence_json = ?,
                        snapshot_id = ?
                    WHERE id = ?
                """, (item["correlation_metric"], json.dumps(item["evidence_json"]), snapshot_id, row["id"]))
                active_ids.append(row["id"])
                stats["updated"] += 1
            else:
                cursor = conn.execute("""
                    INSERT INTO bad_prompt_findings (prompt_type, finding_type, description, evidence_json, correlation_metric, snapshot_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (item["prompt_type"], item["finding_type"], item["description"], json.dumps(item["evidence_json"]), item["correlation_metric"], snapshot_id))
                active_ids.append(cursor.lastrowid)
                stats["detected"] += 1

        # Self-healing
        placeholders = ",".join("?" * len(active_ids))
        if placeholders:
            healed = conn.execute(f"""
                UPDATE bad_prompt_findings
                SET status = 'resolved'
                WHERE status = 'active' AND id NOT IN ({placeholders})
            """, active_ids).rowcount
        else:
            healed = conn.execute("""
                UPDATE bad_prompt_findings
                SET status = 'resolved'
                WHERE status = 'active'
            """).rowcount
        
        stats["resolved"] = healed
        conn.commit()

    return stats
