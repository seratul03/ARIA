import sqlite3
import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

WEAKNESS_TYPES = {
    "category_blind_spot": "A root cause category exists with many active failure patterns but zero implemented hypotheses and zero active engineering rules — ARIA has identified the problem but has no knowledge or plan to address it.",
    "predictor_drift":      "An active predictor's actual_accuracy has dropped more than DRIFT_THRESHOLD below its test_accuracy, indicating the training data no longer reflects current conditions.",
    "hypothesis_stall":     "A hypothesis has been attempted MAX_HYPOTHESIS_ATTEMPTS times without a durable deployment — the proposed fix direction is not working.",
    "population_collapse":  "For a given tool, evolution_population shows that one strategy has won > CONVERGENCE_FRACTION of runs for the last N cycles — diversity has collapsed.",
    "rule_coverage_gap":    "One or more RootCauseCategory values have zero active engineering rules while having active failure patterns — Phase 3 knowledge exists nowhere to guide fixes for this category.",
    "memory_rot":           "Failure patterns with memory_score below MEMORY_ROT_THRESHOLD and occurrence_count above ROT_MIN_OCCURRENCES — Phase 1 memory exists but is no longer useful (stale, low-score, unresolved patterns accumulating).",
    "self_model_lag":       "The gap between overall_deploy_rate at the last snapshot and the rolling 10-cycle deploy rate is > LAG_THRESHOLD — the self-model is not tracking actual performance.",
    "token_concentration":  "Phase 5 predictor feature importances show one feature contributing > CONCENTRATION_THRESHOLD of total importance — the success predictor has overfit to a single signal.",
}

DRIFT_THRESHOLD           = 0.10
CONVERGENCE_FRACTION      = 0.80
MEMORY_ROT_THRESHOLD      = 0.20
ROT_MIN_OCCURRENCES       = 5
LAG_THRESHOLD             = 0.15
CONCENTRATION_THRESHOLD   = 0.35
MAX_HYPOTHESIS_ATTEMPTS   = 3

SEVERITY_RULES = {
    "category_blind_spot": lambda evidence: (
        "critical" if evidence.get("active_pattern_count", 0) >= 10
        else "high"   if evidence.get("active_pattern_count", 0) >= 5
        else "medium"
    ),
    "predictor_drift": lambda evidence: (
        "high"   if evidence.get("accuracy_drop", 0) > 0.20
        else "medium"
    ),
    "hypothesis_stall": lambda evidence: (
        "high"   if evidence.get("attempt_count", 0) >= MAX_HYPOTHESIS_ATTEMPTS
        else "medium"
    ),
    "population_collapse": lambda evidence: "medium",
    "rule_coverage_gap":   lambda evidence: "medium",
    "memory_rot":          lambda evidence: (
        "low"    if evidence.get("rot_pattern_count", 0) <= 3
        else "medium"
    ),
    "self_model_lag":      lambda evidence: (
        "high"   if evidence.get("lag", 0) > LAG_THRESHOLD * 2
        else "medium"
    ),
    "token_concentration": lambda evidence: (
        "high"   if evidence.get("top_feature_importance", 0) > 0.50
        else "medium"
    ),
}

def detect_architectural_weaknesses(db_path: str, snapshot_id: int) -> dict:
    """
    Runs all 8 weakness detectors.
    Returns {"detected": N, "resolved": M, "updated": K}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    
    current_weaknesses = []

    # Helper to load data robustly
    def run_query(sql, params=()):
        try:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        except sqlite3.OperationalError:
            return []

    # -------------------------------------------------------------------------
    # Helper: Pre-load active pattern count by category
    # -------------------------------------------------------------------------
    category_pattern_counts = {}
    try:
        clusters = run_query("SELECT id, root_cause_category, pattern_ids FROM root_cause_clusters")
        active_patterns = {r['id'] for r in run_query("SELECT id FROM failure_patterns WHERE status = 'active'")}
        
        for cluster in clusters:
            cat = cluster["root_cause_category"]
            pids_str = cluster.get("pattern_ids", "[]")
            try:
                pids = json.loads(pids_str)
            except json.JSONDecodeError:
                pids = []
            
            # Count active patterns in this cluster
            active_in_cluster = sum(1 for pid in pids if pid in active_patterns)
            category_pattern_counts[cat] = category_pattern_counts.get(cat, 0) + active_in_cluster
    except Exception as e:
        logger.warning(f"Failed to compute category pattern counts: {e}")

    # -------------------------------------------------------------------------
    # 1. category_blind_spot
    # -------------------------------------------------------------------------
    try:
        # Load rules and hypotheses
        rules = run_query("SELECT category FROM engineering_rules WHERE status = 'active'")
        active_rule_cats = {r["category"] for r in rules}
        
        implemented_hypos = run_query("SELECT source_type, source_id FROM hypotheses WHERE status = 'implemented'")
        impl_hypo_clusters = {h["source_id"] for h in implemented_hypos if h["source_type"] == 'cluster'}
        
        # Determine categories with implemented hypotheses
        impl_hypo_cats = set()
        for cluster in clusters:
            if cluster["id"] in impl_hypo_clusters:
                impl_hypo_cats.add(cluster["root_cause_category"])
        
        for cat, count in category_pattern_counts.items():
            if count > 0 and cat not in active_rule_cats and cat not in impl_hypo_cats:
                current_weaknesses.append({
                    "weakness_type": "category_blind_spot",
                    "title": f"Blind spot for root cause: {cat}",
                    "evidence_json": {
                        "category": cat,
                        "active_pattern_count": count
                    }
                })
    except Exception as e:
        logger.warning(f"category_blind_spot detection failed: {e}")

    # -------------------------------------------------------------------------
    # 5. rule_coverage_gap
    # -------------------------------------------------------------------------
    try:
        for cat, count in category_pattern_counts.items():
            # If it's already a blind spot, skip to avoid spamming the same core issue
            is_blind_spot = (count > 0 and cat not in active_rule_cats and cat not in impl_hypo_cats)
            if not is_blind_spot and count > 0 and cat not in active_rule_cats:
                current_weaknesses.append({
                    "weakness_type": "rule_coverage_gap",
                    "title": f"No active rules for category: {cat}",
                    "evidence_json": {
                        "category": cat,
                        "active_pattern_count": count
                    }
                })
    except Exception as e:
        logger.warning(f"rule_coverage_gap detection failed: {e}")

    # -------------------------------------------------------------------------
    # 2. predictor_drift
    # -------------------------------------------------------------------------
    try:
        snapshot_row = run_query("SELECT predictor_summary_json FROM self_model_snapshots WHERE id = ?", (snapshot_id,))
        if snapshot_row:
            summary_str = snapshot_row[0].get("predictor_summary_json", "{}")
            if summary_str:
                summary = json.loads(summary_str)
                for ptype, pinfo in summary.items():
                    if pinfo.get("status") == "active":
                        test_acc = pinfo.get("test_accuracy")
                        act_acc = pinfo.get("actual_accuracy")
                        if test_acc is not None and act_acc is not None:
                            drop = test_acc - act_acc
                            if drop > DRIFT_THRESHOLD:
                                current_weaknesses.append({
                                    "weakness_type": "predictor_drift",
                                    "title": f"Predictor drift in {ptype} model",
                                    "evidence_json": {
                                        "predictor_type": ptype,
                                        "test_accuracy": test_acc,
                                        "actual_accuracy": act_acc,
                                        "accuracy_drop": drop
                                    }
                                })
    except Exception as e:
        logger.warning(f"predictor_drift detection failed: {e}")

    # -------------------------------------------------------------------------
    # 3. hypothesis_stall
    # -------------------------------------------------------------------------
    try:
        stalled = run_query("SELECT id, attempt_count FROM hypotheses WHERE status != 'implemented' AND attempt_count >= ?", (MAX_HYPOTHESIS_ATTEMPTS,))
        for row in stalled:
            current_weaknesses.append({
                "weakness_type": "hypothesis_stall",
                "title": f"Hypothesis {row['id']} stalled after {row['attempt_count']} attempts",
                "evidence_json": {
                    "hypothesis_id": row["id"],
                    "attempt_count": row["attempt_count"]
                }
            })
    except Exception as e:
        logger.warning(f"hypothesis_stall detection failed: {e}")

    # -------------------------------------------------------------------------
    # 4. population_collapse
    # -------------------------------------------------------------------------
    try:
        # Check last 10 runs per tool
        tools = run_query("SELECT DISTINCT tool_name FROM evolution_runs WHERE winner_candidate_id IS NOT NULL")
        for t_row in tools:
            t = t_row["tool_name"]
            runs = run_query("""
                SELECT ec.strategy_name 
                FROM evolution_runs er 
                JOIN evolution_candidates ec ON er.winner_candidate_id = ec.id 
                WHERE er.tool_name = ? AND er.winner_candidate_id IS NOT NULL 
                ORDER BY er.started_at DESC LIMIT 10
            """, (t,))
            if len(runs) >= 5:
                strats = [r["strategy_name"] for r in runs if "strategy_name" in r]
                if strats:
                    from collections import Counter
                    c = Counter(strats)
                    most_common, mc_count = c.most_common(1)[0]
                    frac = mc_count / len(strats)
                    if frac > CONVERGENCE_FRACTION:
                        current_weaknesses.append({
                            "weakness_type": "population_collapse",
                            "title": f"Population collapse in {t} ({most_common} dominating)",
                            "evidence_json": {
                                "tool_name": t,
                                "dominant_strategy": most_common,
                                "win_fraction": frac
                            }
                        })
    except Exception as e:
        logger.warning(f"population_collapse detection failed: {e}")

    # -------------------------------------------------------------------------
    # 6. memory_rot
    # -------------------------------------------------------------------------
    try:
        rot_patterns = run_query("""
            SELECT fp.id, fp.occurrence_count, fh.memory_score 
            FROM failure_patterns fp 
            JOIN failure_history fh ON fp.representative_failure_id = fh.id 
            WHERE fp.status = 'active' AND fh.memory_score < ? AND fp.occurrence_count > ?
        """, (MEMORY_ROT_THRESHOLD, ROT_MIN_OCCURRENCES))
        
        if rot_patterns:
            current_weaknesses.append({
                "weakness_type": "memory_rot",
                "title": f"Memory rot detected across {len(rot_patterns)} active patterns",
                "evidence_json": {
                    "rot_pattern_count": len(rot_patterns),
                    "patterns": [{"id": r["id"], "score": r["memory_score"], "count": r["occurrence_count"]} for r in rot_patterns]
                }
            })
    except Exception as e:
        logger.warning(f"memory_rot detection failed: {e}")

    # -------------------------------------------------------------------------
    # 7. self_model_lag
    # -------------------------------------------------------------------------
    try:
        snapshot_row = run_query("SELECT overall_deploy_rate FROM self_model_snapshots WHERE id = ?", (snapshot_id,))
        if snapshot_row and snapshot_row[0].get("overall_deploy_rate") is not None:
            snapshot_rate = float(snapshot_row[0]["overall_deploy_rate"])
            
            recent = run_query("SELECT result FROM improvement_history ORDER BY id DESC LIMIT 10")
            if len(recent) >= 5:
                dep_count = sum(1 for r in recent if r["result"] == "deployed")
                rolling = dep_count / len(recent)
                
                lag = abs(snapshot_rate - rolling)
                if lag > LAG_THRESHOLD:
                    current_weaknesses.append({
                        "weakness_type": "self_model_lag",
                        "title": f"Self-model deploy rate lagging actual by {lag:.2f}",
                        "evidence_json": {
                            "overall_deploy_rate": snapshot_rate,
                            "rolling_10_deploy_rate": rolling,
                            "lag": lag
                        }
                    })
    except Exception as e:
        logger.warning(f"self_model_lag detection failed: {e}")

    # -------------------------------------------------------------------------
    # 8. token_concentration
    # -------------------------------------------------------------------------
    try:
        predictors = run_query("SELECT predictor_type, notes FROM predictor_registry WHERE status = 'active'")
        for pred in predictors:
            notes_str = pred.get("notes", "{}")
            if notes_str:
                notes = json.loads(notes_str)
                top_10 = notes.get("top_10_features", [])
                if top_10:
                    top_feature = top_10[0]
                    if top_feature.get("importance", 0) > CONCENTRATION_THRESHOLD:
                        current_weaknesses.append({
                            "weakness_type": "token_concentration",
                            "title": f"Overfitted predictor: {pred['predictor_type']} concentrated on {top_feature['feature']}",
                            "evidence_json": {
                                "predictor_type": pred["predictor_type"],
                                "top_feature": top_feature["feature"],
                                "top_feature_importance": top_feature["importance"]
                            }
                        })
    except Exception as e:
        logger.warning(f"token_concentration detection failed: {e}")

    # -------------------------------------------------------------------------
    # Merge with existing rows
    # -------------------------------------------------------------------------
    
    # We define a "match key" to identify if an existing row represents the same weakness.
    def get_match_key(w):
        t = w["weakness_type"]
        ev = w["evidence_json"]
        if t == "category_blind_spot" or t == "rule_coverage_gap":
            return (t, ev["category"])
        elif t == "predictor_drift" or t == "token_concentration":
            return (t, ev["predictor_type"])
        elif t == "hypothesis_stall":
            return (t, ev["hypothesis_id"])
        elif t == "population_collapse":
            return (t, ev["tool_name"])
        elif t == "memory_rot":
            return (t, "global")  # only one global rot record
        elif t == "self_model_lag":
            return (t, "global")  # only one global lag record
        return (t, "unknown")

    # Map current detected
    current_map = {get_match_key(w): w for w in current_weaknesses}
    
    # Fetch existing active
    existing_active_rows = run_query("SELECT * FROM architectural_weaknesses WHERE status = 'active'")
    existing_map = {}
    for row in existing_active_rows:
        try:
            ev = json.loads(row["evidence_json"])
        except:
            ev = {}
        # reconstructing the match key
        dummy_w = {"weakness_type": row["weakness_type"], "evidence_json": ev}
        existing_map[get_match_key(dummy_w)] = row

    stats = {"detected": 0, "resolved": 0, "updated": 0}

    cursor = conn.cursor()
    import time
    
    # 1. Resolve old ones that are no longer true
    for key, row in existing_map.items():
        if key not in current_map:
            cursor.execute("UPDATE architectural_weaknesses SET status = 'addressed', last_updated_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            stats["resolved"] += 1

    # 2. Insert or Update current ones
    for key, w in current_map.items():
        severity = SEVERITY_RULES[w["weakness_type"]](w["evidence_json"])
        ev_str = json.dumps(w["evidence_json"])
        
        if key in existing_map:
            row_id = existing_map[key]["id"]
            cursor.execute("""
                UPDATE architectural_weaknesses 
                SET last_updated_at = CURRENT_TIMESTAMP, evidence_json = ?, severity = ?, snapshot_id = ?
                WHERE id = ?
            """, (ev_str, severity, snapshot_id, row_id))
            stats["updated"] += 1
        else:
            cursor.execute("""
                INSERT INTO architectural_weaknesses 
                (weakness_type, title, evidence_json, severity, status, snapshot_id) 
                VALUES (?, ?, ?, ?, 'active', ?)
            """, (w["weakness_type"], w["title"], ev_str, severity, snapshot_id))
            stats["detected"] += 1

    conn.commit()
    conn.close()

    return stats
