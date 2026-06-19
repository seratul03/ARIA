import json
import logging
import sqlite3
from typing import Dict, Any, List
from collections import defaultdict

from aria.reflection.weaknesses import MAX_HYPOTHESIS_ATTEMPTS

logger = logging.getLogger(__name__)

MISTAKE_TYPES = {
    "rule_violation_pattern": "engineering_rules that are 'active' but have rule_compliance_score = 0.0 in > RULE_VIOLATION_RATE fraction of the cycles.",
    "target_selection_oscillation": "The same tool has been selected as the target in more than OSCILLATION_FRACTION of the last N cycles, without improving deploy rate.",
    "malformed_code_recurrence": "disqualified candidates with static_analysis failure appear in > MALFORM_RATE fraction of evolution runs.",
    "hypothesis_direction_failure": "Hypotheses of a specific category have attempt_count reaching MAX_HYPOTHESIS_ATTEMPTS without deployment at a high rate.",
    "breeding_parent_reuse": "The same candidate_id appearing as a parent in multiple breeding operations across multiple runs.",
    "post_deploy_regression_cluster": "More than REGRESSION_CLUSTER_THRESHOLD fraction of rolled-back improvements share the same root_cause_category.",
}

RULE_VIOLATION_RATE       = 0.40
OSCILLATION_FRACTION      = 0.60
MALFORM_RATE              = 0.25
REGRESSION_CLUSTER_THRESHOLD = 0.50
PARENT_REUSE_THRESHOLD    = 3

def detect_recurring_mistakes(db_path: str, snapshot_id: int, lookback_cycles: int = 20) -> dict:
    detected_mistakes = []

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = lambda cursor, row: {col[0]: row[idx] for idx, col in enumerate(cursor.description)}

        # Helper to fetch recent completed evolution runs
        recent_runs_query = f"SELECT id FROM evolution_runs WHERE run_status = 'completed' ORDER BY id DESC LIMIT {lookback_cycles}"
        recent_runs = [r["id"] for r in conn.execute(recent_runs_query).fetchall()]
        total_runs = len(recent_runs)

        if total_runs > 0:
            # 1. rule_violation_pattern
            rv_rows = conn.execute(f"""
                WITH recent_runs AS (
                    SELECT id, cycle_id, winner_candidate_id
                    FROM evolution_runs
                    WHERE run_status = 'completed'
                    ORDER BY id DESC LIMIT {lookback_cycles}
                ),
                applicable_rules AS (
                    SELECT ra.rule_id, r.id as run_id
                    FROM rule_applications ra
                    JOIN recent_runs r ON ra.cycle_id = r.cycle_id
                )
                SELECT ar.rule_id, er.rule_text, COUNT(DISTINCT ar.run_id) as total_applicable,
                       SUM(CASE WHEN ec.rule_compliance_score = 0.0 THEN 1 ELSE 0 END) as violated_count
                FROM applicable_rules ar
                JOIN recent_runs r ON ar.run_id = r.id
                JOIN evolution_candidates ec ON r.winner_candidate_id = ec.id
                JOIN engineering_rules er ON ar.rule_id = er.id
                WHERE er.status = 'active'
                GROUP BY ar.rule_id
            """).fetchall()

            for r in rv_rows:
                if r["total_applicable"] > 0:
                    violation_rate = r["violated_count"] / r["total_applicable"]
                    if violation_rate > RULE_VIOLATION_RATE:
                        detected_mistakes.append({
                            "mistake_type": "rule_violation_pattern",
                            "description": f"Rule {r['rule_id']} violated in {violation_rate:.0%} of applicable runs.",
                            "evidence_json": {
                                "rule_id": r["rule_id"],
                                "violation_rate": violation_rate,
                                "total_applicable": r["total_applicable"],
                                "violated_count": r["violated_count"]
                            }
                        })

            # 2. target_selection_oscillation
            osc_rows = conn.execute(f"""
                WITH recent_runs AS (
                    SELECT tool_name 
                    FROM evolution_runs
                    WHERE run_status = 'completed'
                    ORDER BY id DESC LIMIT {lookback_cycles}
                )
                SELECT tool_name, COUNT(*) as selection_count
                FROM recent_runs
                GROUP BY tool_name
            """).fetchall()

            for r in osc_rows:
                if r["selection_count"] / total_runs > OSCILLATION_FRACTION:
                    # Check deploy rate for tool (simple heuristic: has it had any deployments recently?)
                    dep_row = conn.execute("""
                        SELECT COUNT(*) as deps 
                        FROM improvement_history 
                        WHERE tool_name = ? AND result = 'deployed' AND id > (
                            SELECT IFNULL(MAX(id) - 100, 0) FROM improvement_history
                        )
                    """, (r["tool_name"],)).fetchone()
                    
                    if dep_row and dep_row["deps"] == 0:
                        detected_mistakes.append({
                            "mistake_type": "target_selection_oscillation",
                            "description": f"Tool '{r['tool_name']}' targeted {r['selection_count']}/{total_runs} times with no recent deployments.",
                            "evidence_json": {
                                "tool_name": r["tool_name"],
                                "consecutive_selections": r["selection_count"],
                                "recent_deployments": 0
                            }
                        })

            # 3. malformed_code_recurrence
            mal_row = conn.execute(f"""
                WITH recent_runs AS (
                    SELECT id FROM evolution_runs
                    WHERE run_status = 'completed'
                    ORDER BY id DESC LIMIT {lookback_cycles}
                )
                SELECT COUNT(DISTINCT evolution_run_id) as malformed_runs
                FROM evolution_candidates
                WHERE evolution_run_id IN (SELECT id FROM recent_runs)
                  AND disqualified = 1 
                  AND disqualification_reason LIKE '%static_analysis%'
            """).fetchone()

            if mal_row:
                malform_rate = mal_row["malformed_runs"] / total_runs
                if malform_rate > MALFORM_RATE:
                    detected_mistakes.append({
                        "mistake_type": "malformed_code_recurrence",
                        "description": f"Static analysis failed in {malform_rate:.0%} of recent evolution runs.",
                        "evidence_json": {
                            "malform_rate": malform_rate,
                            "malformed_runs": mal_row["malformed_runs"],
                            "total_runs": total_runs
                        }
                    })

            # 4. hypothesis_direction_failure
            hyp_rows = conn.execute(f"""
                SELECT rcc.root_cause_category,
                       COUNT(h.id) as total_hypotheses,
                       SUM(CASE WHEN h.attempt_count >= ? OR h.status IN ('abandoned', 'failed') THEN 1 ELSE 0 END) as failed_hypotheses
                FROM hypotheses h
                JOIN root_cause_clusters rcc ON h.source_id = rcc.id AND h.source_type = 'cluster'
                GROUP BY rcc.root_cause_category
            """, (MAX_HYPOTHESIS_ATTEMPTS,)).fetchall()

            for r in hyp_rows:
                if r["total_hypotheses"] >= 3: # Need minimum sample
                    failure_rate = r["failed_hypotheses"] / r["total_hypotheses"]
                    if failure_rate > 0.50:
                        detected_mistakes.append({
                            "mistake_type": "hypothesis_direction_failure",
                            "description": f"Category '{r['root_cause_category']}' has a hypothesis failure rate of {failure_rate:.0%}.",
                            "evidence_json": {
                                "category": r["root_cause_category"],
                                "failure_rate_vs_baseline": failure_rate,
                                "failed_hypotheses": r["failed_hypotheses"],
                                "total_hypotheses": r["total_hypotheses"]
                            }
                        })

            # 5. breeding_parent_reuse
            bred_rows = conn.execute(f"""
                SELECT er.tool_name, ec.strategy 
                FROM evolution_candidates ec
                JOIN evolution_runs er ON ec.evolution_run_id = er.id
                WHERE ec.strategy LIKE 'bred:%'
                  AND er.id IN (SELECT id FROM evolution_runs WHERE run_status = 'completed' ORDER BY id DESC LIMIT {lookback_cycles})
            """).fetchall()

            parent_counts = defaultdict(lambda: defaultdict(int))
            for r in bred_rows:
                strategy = r["strategy"].replace("bred:", "")
                parts = strategy.split("+")
                for p in parts:
                    if p.isdigit():
                        parent_counts[r["tool_name"]][p] += 1

            for tool, counts in parent_counts.items():
                for p, count in counts.items():
                    if count >= PARENT_REUSE_THRESHOLD:
                        detected_mistakes.append({
                            "mistake_type": "breeding_parent_reuse",
                            "description": f"Parent candidate {p} reused {count} times for {tool}.",
                            "evidence_json": {
                                "tool_name": tool,
                                "dominant_parent_candidate_id": int(p),
                                "reuse_count": count
                            }
                        })

        # 6. post_deploy_regression_cluster
        recent_rollbacks = conn.execute(f"""
            SELECT weakness_category 
            FROM improvement_history
            WHERE result = 'rolled_back'
            ORDER BY id DESC LIMIT {lookback_cycles}
        """).fetchall()

        total_rollbacks = len(recent_rollbacks)
        if total_rollbacks >= 3:
            rollback_counts = defaultdict(int)
            for r in recent_rollbacks:
                if r["weakness_category"]:
                    rollback_counts[r["weakness_category"]] += 1

            for cat, count in rollback_counts.items():
                rate = count / total_rollbacks
                if rate > REGRESSION_CLUSTER_THRESHOLD:
                    detected_mistakes.append({
                        "mistake_type": "post_deploy_regression_cluster",
                        "description": f"{rate:.0%} of recent rollbacks belong to category '{cat}'.",
                        "evidence_json": {
                            "category": cat,
                            "regression_rate": rate,
                            "rollback_count": count,
                            "total_rollbacks": total_rollbacks
                        }
                    })

        # UPSERT logic
        stats = {"detected": 0, "resolved": 0, "updated": 0}
        active_mistake_ids = []

        for mistake in detected_mistakes:
            # Check if exists
            row = conn.execute("""
                SELECT id FROM recurring_mistakes 
                WHERE mistake_type = ? AND status = 'active'
                AND json_extract(evidence_json, '$.rule_id') IS json_extract(?, '$.rule_id')
                AND json_extract(evidence_json, '$.tool_name') IS json_extract(?, '$.tool_name')
                AND json_extract(evidence_json, '$.category') IS json_extract(?, '$.category')
                AND json_extract(evidence_json, '$.dominant_parent_candidate_id') IS json_extract(?, '$.dominant_parent_candidate_id')
            """, (mistake["mistake_type"], 
                  json.dumps(mistake["evidence_json"]), 
                  json.dumps(mistake["evidence_json"]), 
                  json.dumps(mistake["evidence_json"]),
                  json.dumps(mistake["evidence_json"]))).fetchone()

            if row:
                conn.execute("""
                    UPDATE recurring_mistakes 
                    SET last_seen_at = CURRENT_TIMESTAMP,
                        evidence_json = ?,
                        snapshot_id = ?,
                        occurrence_count = occurrence_count + 1
                    WHERE id = ?
                """, (json.dumps(mistake["evidence_json"]), snapshot_id, row["id"]))
                active_mistake_ids.append(row["id"])
                stats["updated"] += 1
            else:
                cursor = conn.execute("""
                    INSERT INTO recurring_mistakes (mistake_type, description, evidence_json, snapshot_id)
                    VALUES (?, ?, ?, ?)
                """, (mistake["mistake_type"], mistake["description"], json.dumps(mistake["evidence_json"]), snapshot_id))
                active_mistake_ids.append(cursor.lastrowid)
                stats["detected"] += 1

        # Self-healing
        placeholders = ",".join("?" * len(active_mistake_ids))
        if placeholders:
            healed = conn.execute(f"""
                UPDATE recurring_mistakes
                SET status = 'resolved'
                WHERE status = 'active' AND id NOT IN ({placeholders})
            """, active_mistake_ids).rowcount
        else:
            healed = conn.execute("""
                UPDATE recurring_mistakes
                SET status = 'resolved'
                WHERE status = 'active'
            """).rowcount
        
        stats["resolved"] = healed
        conn.commit()

    return stats
