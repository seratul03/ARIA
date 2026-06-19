import json
import logging
import sqlite3
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DISQ_WASTE_THRESHOLD = 0.40
RETRIEVAL_AGE_THRESHOLD = 30
BARREN_CYCLE_RATE = 0.30

# For proxy estimates
AVG_TOKENS_PER_GENERATION = 2000
CHARS_PER_TOKEN = 4
TOKENS_PER_CLASSIFICATION = 800

def ensure_token_tracking(db_path: str) -> bool:
    """
    Check if cycle_traces has a 'tokens_used' column. If not, add it via
    ALTER TABLE. Return True if tracking is available. If False, all token
    waste findings use proxy metrics.
    """
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute("SELECT tokens_used FROM cycle_traces LIMIT 1")
            return True
        except sqlite3.OperationalError:
            try:
                conn.execute("ALTER TABLE cycle_traces ADD COLUMN tokens_used INTEGER")
                return True
            except sqlite3.OperationalError as e:
                logger.warning(f"Failed to add tokens_used to cycle_traces: {e}")
                return False

def analyze_token_waste(db_path: str, snapshot_id: int) -> dict:
    has_tracking = ensure_token_tracking(db_path)
    
    findings = []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
        
        # 1. high_disqualification_generation
        disq_rows = conn.execute(f"""
            WITH recent_runs AS (
                SELECT id FROM evolution_runs
                WHERE run_status = 'completed'
                ORDER BY id DESC LIMIT 20
            ),
            run_stats AS (
                SELECT ec.evolution_run_id, 
                       COUNT(*) as total_candidates,
                       SUM(CASE WHEN ec.disqualified = 1 THEN 1 ELSE 0 END) as disq_count
                FROM evolution_candidates ec
                JOIN recent_runs r ON ec.evolution_run_id = r.id
                GROUP BY ec.evolution_run_id
            )
            SELECT evolution_run_id, total_candidates, disq_count
            FROM run_stats
            WHERE disq_count * 1.0 / total_candidates > {DISQ_WASTE_THRESHOLD}
        """).fetchall()
        
        if disq_rows:
            total_disq = sum(r['disq_count'] for r in disq_rows)
            tokens_wasted = total_disq * AVG_TOKENS_PER_GENERATION
            findings.append({
                "waste_type": "high_disqualification_generation",
                "description": "High rate of candidates disqualified by static analysis.",
                "estimated_tokens_wasted_per_cycle": tokens_wasted / len(disq_rows),
                "evidence_json": {
                    "affected_runs_count": len(disq_rows),
                    "total_disqualified": total_disq,
                    "is_proxy_estimate": not has_tracking
                },
                "severity": "high" if (total_disq / (sum(r['total_candidates'] for r in disq_rows) or 1)) > 0.6 else "medium"
            })

        # 2. redundant_retrieval_context
        try:
            red_rows = conn.execute(f"""
                WITH old_fixes AS (
                    SELECT tool_name, COUNT(*) as old_count
                    FROM improvement_history
                    WHERE timestamp < datetime('now', '-{RETRIEVAL_AGE_THRESHOLD} days')
                      AND result = 'deployed' AND tool_name IS NOT NULL
                    GROUP BY tool_name
                ),
                new_fixes AS (
                    SELECT tool_name, COUNT(*) as new_count
                    FROM improvement_history
                    WHERE timestamp >= datetime('now', '-{RETRIEVAL_AGE_THRESHOLD} days')
                      AND result = 'deployed' AND tool_name IS NOT NULL
                    GROUP BY tool_name
                )
                SELECT o.tool_name, o.old_count, n.new_count
                FROM old_fixes o
                JOIN new_fixes n ON o.tool_name = n.tool_name
            """).fetchall()
            
            for r in red_rows:
                findings.append({
                    "waste_type": "redundant_retrieval_context",
                    "description": f"Tool {r['tool_name']} has old retrieved context but was recently rewritten.",
                    "estimated_tokens_wasted_per_cycle": (r['old_count'] * 1000) / CHARS_PER_TOKEN,
                    "evidence_json": {
                        "tool_name": r["tool_name"],
                        "old_fixes_count": r["old_count"],
                        "new_fixes_count": r["new_count"],
                        "is_proxy_estimate": not has_tracking
                    },
                    "severity": "low"
                })
        except sqlite3.OperationalError:
            pass

        # 3. hypothesis_llm_overspend
        try:
            h_rows = conn.execute("""
                SELECT 
                    SUM(CASE WHEN classification_source = 'llm' THEN 1 ELSE 0 END) as llm_count,
                    COUNT(*) as total_count
                FROM failure_patterns
                WHERE status = 'active'
            """).fetchone()
            
            if h_rows and h_rows["total_count"] > 0:
                llm_fraction = h_rows["llm_count"] / h_rows["total_count"]
                if llm_fraction > 0.5:
                    excess_llm = h_rows["llm_count"] - (0.5 * h_rows["total_count"])
                    findings.append({
                        "waste_type": "hypothesis_llm_overspend",
                        "description": "Heuristic coverage for failure patterns is < 50%, overusing LLM.",
                        "estimated_tokens_wasted_per_cycle": float(excess_llm * TOKENS_PER_CLASSIFICATION),
                        "evidence_json": {
                            "llm_classified_count": h_rows["llm_count"],
                            "total_classified": h_rows["total_count"],
                            "is_proxy_estimate": not has_tracking
                        },
                        "severity": "medium"
                    })
        except sqlite3.OperationalError:
            pass
            
        # 4. low_yield_meta_cycle
        try:
            m_rows = conn.execute("""
                WITH meta_cycles AS (
                    SELECT id, result FROM improvement_history WHERE improvement_type = 'meta'
                    ORDER BY id DESC LIMIT 20
                )
                SELECT COUNT(*) as total_meta,
                       SUM(CASE WHEN result IN ('rejected', 'rolled_back') THEN 1 ELSE 0 END) as barren_meta
                FROM meta_cycles
            """).fetchone()
            
            if m_rows and m_rows["total_meta"] > 0:
                barren_rate = m_rows["barren_meta"] / m_rows["total_meta"]
                if barren_rate >= BARREN_CYCLE_RATE:
                    findings.append({
                        "waste_type": "low_yield_meta_cycle",
                        "description": "Meta-introspection cycles are running too frequently without yield.",
                        "estimated_tokens_wasted_per_cycle": 5000.0 * barren_rate,
                        "evidence_json": {
                            "barren_fraction": barren_rate,
                            "total_meta_cycles_checked": m_rows["total_meta"],
                            "is_proxy_estimate": not has_tracking
                        },
                        "severity": "medium"
                    })
        except sqlite3.OperationalError:
            pass

        # UPSERT logic
        stats = {"detected": 0, "resolved": 0, "updated": 0}
        active_ids = []

        for item in findings:
            row = conn.execute("""
                SELECT id FROM token_waste_findings 
                WHERE waste_type = ? AND status = 'active'
            """, (item["waste_type"],)).fetchone()

            if row:
                conn.execute("""
                    UPDATE token_waste_findings 
                    SET last_updated_at = CURRENT_TIMESTAMP,
                        estimated_tokens_wasted_per_cycle = ?,
                        evidence_json = ?,
                        severity = ?,
                        snapshot_id = ?
                    WHERE id = ?
                """, (item["estimated_tokens_wasted_per_cycle"], json.dumps(item["evidence_json"]), item["severity"], snapshot_id, row["id"]))
                active_ids.append(row["id"])
                stats["updated"] += 1
            else:
                cursor = conn.execute("""
                    INSERT INTO token_waste_findings (waste_type, description, estimated_tokens_wasted_per_cycle, evidence_json, severity, snapshot_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (item["waste_type"], item["description"], item["estimated_tokens_wasted_per_cycle"], json.dumps(item["evidence_json"]), item["severity"], snapshot_id))
                active_ids.append(cursor.lastrowid)
                stats["detected"] += 1

        # Self-healing
        placeholders = ",".join("?" * len(active_ids))
        if placeholders:
            healed = conn.execute(f"""
                UPDATE token_waste_findings
                SET status = 'resolved'
                WHERE status = 'active' AND id NOT IN ({placeholders})
            """, active_ids).rowcount
        else:
            healed = conn.execute("""
                UPDATE token_waste_findings
                SET status = 'resolved'
                WHERE status = 'active'
            """).rowcount
        
        stats["resolved"] = healed
        conn.commit()

    return stats
