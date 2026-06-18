"""
aria/predictors/inference.py
────────────────────────────
Integrates predictors into the live evolution cycle at strategic gating points.
"""

from __future__ import annotations
import sqlite3
import logging
import hashlib
import json
import time

from aria.predictors.registry import get_active_predictor
from aria.predictors.features import (
    compute_cycle_feature_vector,
    compute_feature_vector,
    compute_risk_feature_vector
)

logger = logging.getLogger(__name__)

FAILURE_PREDICTION_SKIP_THRESHOLD = 0.20   # skip cycle if predicted cycle-success prob < this
CANDIDATE_FILTER_THRESHOLD = 0.25           # filter out candidates predicted < this success prob
                                             # (but only if >= MIN_SURVIVING_CANDIDATES remain)
MIN_SURVIVING_CANDIDATES = 2               # never filter to fewer than this
RISK_THRESHOLD = 0.40                      # above this: flag for human review before deployment

def _log_prediction(
    db_path: str,
    predictor_id: int,
    prediction_type: str,
    predicted_value: float,
    predicted_confidence: float,
    feature_vector_hash: str,
    evolution_run_id: int | None = None,
    candidate_id: int | None = None,
    tool_name: str | None = None
) -> None:
    try:
        conn = sqlite3.connect(db_path)
        conn.execute(
            """
            INSERT INTO prediction_log (
                predictor_id, evolution_run_id, candidate_id, tool_name,
                prediction_type, predicted_value, predicted_confidence,
                actual_value, feature_vector_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                predictor_id, evolution_run_id, candidate_id, tool_name,
                prediction_type, str(predicted_value), predicted_confidence,
                "pending", feature_vector_hash
            )
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log prediction: {e}")
    finally:
        conn.close()

def predict_cycle_viability(tool_name: str, run_context: dict, db_path: str) -> dict:
    """
    Gate 1: Predict if the cycle will succeed before doing any work.
    """
    res = get_active_predictor('failure', db_path)
    if not res:
        return {"skip": False, "predicted_success_prob": 1.0, "confidence": None, "predictor_id": None}
    
    model, pred_id = res
    
    # We construct a mock run_row for the cycle predictor
    run_row = {
        "tool_name": tool_name,
        "started_at": time.time(),
        "hypothesis_id": run_context.get("hypothesis_id"),
        "root_cause_category": run_context.get("root_cause_category")
    }
    
    vec = compute_cycle_feature_vector(run_row, db_path)
    try:
        prob = float(model.predict_proba([vec])[0][1])
    except Exception as e:
        logger.error(f"Cycle viability prediction failed: {e}")
        return {"skip": False, "predicted_success_prob": 1.0, "confidence": None, "predictor_id": pred_id}
        
    confidence = abs(prob - 0.5) * 2
    
    # Hash features
    feat_hash = hashlib.sha256(json.dumps(vec).encode('utf-8')).hexdigest()
    
    # evolution_run_id doesn't exist yet, so we just log tool_name
    _log_prediction(
        db_path, pred_id, "failure", prob, confidence, feat_hash,
        evolution_run_id=None, candidate_id=None, tool_name=tool_name
    )
    
    return {
        "skip": prob < FAILURE_PREDICTION_SKIP_THRESHOLD,
        "predicted_success_prob": prob,
        "confidence": confidence,
        "predictor_id": pred_id
    }

def predict_candidate_success(candidates: list[dict], run_context: dict, db_path: str) -> list[dict]:
    """
    Gate 2: Predict candidate success probabilities and filter.
    """
    res = get_active_predictor('success', db_path)
    if not res:
        return candidates
        
    model, pred_id = res
    
    for c in candidates:
        vec, feat_hash = compute_feature_vector(c, run_context, db_path)
        try:
            prob = float(model.predict_proba([vec])[0][1])
        except Exception as e:
            logger.error(f"Candidate success prediction failed: {e}")
            prob = 1.0
            
        c['predicted_success_prob'] = prob
        
        # Log prediction if candidate has an ID
        if c.get("id"):
            confidence = abs(prob - 0.5) * 2
            _log_prediction(
                db_path, pred_id, "success", prob, confidence, feat_hash,
                evolution_run_id=c.get("evolution_run_id"), candidate_id=c["id"], tool_name=run_context.get("tool_name")
            )
            
    # Sort descending by prob
    sorted_candidates = sorted(candidates, key=lambda x: x.get('predicted_success_prob', 1.0), reverse=True)
    
    filtered_candidates = []
    for i, c in enumerate(sorted_candidates):
        if c.get('predicted_success_prob', 1.0) >= CANDIDATE_FILTER_THRESHOLD or i < MIN_SURVIVING_CANDIDATES:
            filtered_candidates.append(c)
        else:
            c['predicted_low'] = True
            c['disqualified'] = 1
            c['disqualification_reason'] = f"Predicted success prob {c['predicted_success_prob']:.2f} < threshold"
            
            # Persist disqualification to DB
            if c.get("id"):
                try:
                    conn = sqlite3.connect(db_path)
                    conn.execute(
                        "UPDATE evolution_candidates SET disqualified=1, disqualification_reason=? WHERE id=?",
                        (c['disqualification_reason'], c["id"])
                    )
                    conn.commit()
                    conn.close()
                except Exception as e:
                    logger.error(f"Failed to update disqualified candidate: {e}")
                    
    return filtered_candidates

def predict_deployment_risk(winner_candidate: dict, run_context: dict, db_path: str) -> dict:
    """
    Gate 3: Predict rollback risk for the winning candidate.
    """
    res = get_active_predictor('risk', db_path)
    if not res:
        return {"high_risk": False, "rollback_prob": None, "predictor_id": None}
        
    model, pred_id = res
    
    # Risk features need candidate row plus some context
    candidate_row = dict(winner_candidate)
    candidate_row["tool_name"] = run_context.get("tool_name", "")
    candidate_row["ih_timestamp"] = time.time()
    candidate_row["trigger_is_hypothesis"] = run_context.get("trigger_is_hypothesis", 0.0)
    candidate_row["hypothesis_confidence"] = run_context.get("hypothesis_confidence", 0.5)
    
    vec = compute_risk_feature_vector(candidate_row, db_path)
    try:
        prob = float(model.predict_proba([vec])[0][1])
    except Exception as e:
        logger.error(f"Risk prediction failed: {e}")
        return {"high_risk": False, "rollback_prob": None, "predictor_id": pred_id}
        
    confidence = abs(prob - 0.5) * 2
    feat_hash = hashlib.sha256(json.dumps(vec).encode('utf-8')).hexdigest()
    
    _log_prediction(
        db_path, pred_id, "risk", prob, confidence, feat_hash,
        evolution_run_id=winner_candidate.get("evolution_run_id"),
        candidate_id=winner_candidate.get("id"),
        tool_name=run_context.get("tool_name")
    )
    
    return {
        "high_risk": prob > RISK_THRESHOLD,
        "rollback_prob": prob,
        "predictor_id": pred_id
    }


def resolve_prediction_outcomes(db_path: str) -> dict:
    """
    Run after each cycle concludes. Resolves pending predictions.
    """
    res = {"resolved_success": 0, "resolved_failure": 0, "resolved_risk": 0, "still_pending": 0}
    
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. Success predictions (candidate level)
        # Sandbox must have completed. If winner deployed -> 1 else 0.
        success_rows = conn.execute(
            """
            SELECT pl.id, ec.sandbox_passed, ec.id as c_id, er.winner_candidate_id, er.run_status
            FROM prediction_log pl
            JOIN evolution_candidates ec ON pl.candidate_id = ec.id
            JOIN evolution_runs er ON ec.evolution_run_id = er.id
            WHERE pl.prediction_type='success' AND pl.actual_value='pending'
            """
        ).fetchall()
        
        for r in success_rows:
            if r["sandbox_passed"] is not None: # Sandbox has completed
                # It's a success if it passed sandbox, won, and the run completed (deployed)
                is_success = (r["sandbox_passed"] == 1 and 
                              r["c_id"] == r["winner_candidate_id"] and 
                              r["run_status"] == 'completed')
                actual_val = "1" if is_success else "0"
                conn.execute("UPDATE prediction_log SET actual_value=? WHERE id=?", (actual_val, r["id"]))
                res["resolved_success"] += 1
                
        # 2. Failure predictions (cycle level)
        failure_rows = conn.execute(
            """
            SELECT pl.id, er.run_status, er.winner_candidate_id
            FROM prediction_log pl
            JOIN evolution_runs er ON pl.evolution_run_id = er.id
            WHERE pl.prediction_type='failure' AND pl.actual_value='pending'
            """
        ).fetchall()
        
        # Handle cases where evolution_run_id is NULL but tool_name matches the latest run
        # We skipped setting evolution_run_id in Gate 1 because it didn't exist yet!
        # Let's fix those by linking them to the latest run for that tool
        conn.execute(
            """
            UPDATE prediction_log
            SET evolution_run_id = (
                SELECT id FROM evolution_runs er 
                WHERE er.tool_name = prediction_log.tool_name 
                ORDER BY started_at DESC LIMIT 1
            )
            WHERE prediction_type='failure' AND evolution_run_id IS NULL AND actual_value='pending'
            """
        )
        
        # Re-fetch after fixing NULLs
        failure_rows = conn.execute(
            """
            SELECT pl.id, er.run_status, er.winner_candidate_id
            FROM prediction_log pl
            JOIN evolution_runs er ON pl.evolution_run_id = er.id
            WHERE pl.prediction_type='failure' AND pl.actual_value='pending'
            AND er.run_status IN ('completed', 'aborted', 'skipped_low_viability', 'failed_generation', 'failed_sandbox', 'failed_deployment')
            """
        ).fetchall()
        
        for r in failure_rows:
            is_success = (r["winner_candidate_id"] is not None and r["run_status"] == 'completed')
            actual_val = "1" if is_success else "0"
            conn.execute("UPDATE prediction_log SET actual_value=? WHERE id=?", (actual_val, r["id"]))
            res["resolved_failure"] += 1
            
        # 3. Risk predictions
        # Check if rolled back
        # Since we mock this for now without the full POST_DEPLOY_MONITOR_WINDOW_DAYS,
        # we'll check if a failure_history entry exists for the same tool since the run completed.
        risk_rows = conn.execute(
            """
            SELECT pl.id, er.tool_name, er.started_at
            FROM prediction_log pl
            JOIN evolution_runs er ON pl.evolution_run_id = er.id
            WHERE pl.prediction_type='risk' AND pl.actual_value='pending'
            """
        ).fetchall()
        
        for r in risk_rows:
            # Did a failure occur after started_at?
            # Normally we check improvement_history, but let's check for any failure_history for this tool
            has_rollback = False
            try:
                fail_cnt = conn.execute("SELECT COUNT(*) FROM failure_history WHERE tool_name=? AND timestamp > ?", (r["tool_name"], r["started_at"])).fetchone()[0]
                has_rollback = fail_cnt > 0
            except sqlite3.OperationalError:
                pass # table might not exist in mock DB
                
            # For simplicity, if 7 days passed or has rollback
            current_time = time.time()
            if has_rollback or (current_time - r["started_at"] > 86400 * 7):
                actual_val = "1" if has_rollback else "0"
                conn.execute("UPDATE prediction_log SET actual_value=? WHERE id=?", (actual_val, r["id"]))
                res["resolved_risk"] += 1
                
        pending = conn.execute("SELECT COUNT(*) FROM prediction_log WHERE actual_value='pending'").fetchone()[0]
        res["still_pending"] = pending
        
        conn.commit()
    except Exception as e:
        logger.error(f"Error resolving prediction outcomes: {e}")
    finally:
        conn.close()
        
    return res

def predictor_health_report(db_path: str, window_predictions: int = 50) -> dict:
    """
    Calculates health metrics for active predictors.
    """
    health = {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        active_preds = conn.execute("SELECT * FROM predictor_registry WHERE status='active'").fetchall()
        
        for p in active_preds:
            ptype = p["predictor_type"]
            pid = p["id"]
            
            # Fetch last N resolved predictions
            preds = conn.execute(
                """
                SELECT predicted_value, actual_value 
                FROM prediction_log 
                WHERE predictor_id=? AND actual_value != 'pending' 
                ORDER BY predicted_at DESC LIMIT ?
                """,
                (pid, window_predictions)
            ).fetchall()
            
            if not preds:
                health[ptype] = {
                    "version": p["version"],
                    "test_auc": p["test_auc"],
                    "actual_accuracy": None,
                    "actual_auc": None,
                    "calibration_error": None,
                    "alert": "insufficient_resolved_data"
                }
                continue
                
            correct = 0
            y_true = []
            y_prob = []
            
            for row in preds:
                try:
                    prob = float(row["predicted_value"])
                    actual = int(row["actual_value"])
                except ValueError:
                    continue
                    
                y_true.append(actual)
                y_prob.append(prob)
                pred_label = 1 if prob > 0.5 else 0
                if pred_label == actual:
                    correct += 1
                    
            n_valid = len(y_true)
            if n_valid == 0:
                continue
                
            actual_accuracy = correct / n_valid
            
            actual_auc = None
            if len(set(y_true)) > 1:
                try:
                    from sklearn.metrics import roc_auc_score
                    actual_auc = roc_auc_score(y_true, y_prob)
                except Exception:
                    pass
                    
            # Calibration error (simplified ECE)
            import numpy as np
            bins = np.linspace(0, 1, 11)
            bin_indices = np.digitize(y_prob, bins) - 1
            ece = 0.0
            for i in range(10):
                mask = bin_indices == i
                if np.any(mask):
                    bin_true = np.array(y_true)[mask]
                    bin_prob = np.array(y_prob)[mask]
                    ece += (len(bin_true) / n_valid) * np.abs(np.mean(bin_prob) - np.mean(bin_true))
            
            alert = None
            if p["test_accuracy"] is not None and actual_accuracy < p["test_accuracy"] - 0.10:
                alert = "accuracy_drift"
            elif ece > 0.15:
                alert = "calibration_error"
                
            health[ptype] = {
                "version": p["version"],
                "test_auc": p["test_auc"],
                "test_accuracy": p["test_accuracy"],
                "actual_accuracy": actual_accuracy,
                "actual_auc": actual_auc,
                "calibration_error": ece,
                "alert": alert
            }
            
    except Exception as e:
        logger.error(f"Error calculating predictor health: {e}")
    finally:
        conn.close()
        
    return health
