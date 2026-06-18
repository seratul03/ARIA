import sqlite3
import logging
from typing import Dict, Any
from aria.predictors.features import ALL_FEATURES, compute_feature_vector, FAILURE_FEATURES, compute_cycle_feature_vector, RISK_FEATURES, compute_risk_feature_vector

logger = logging.getLogger(__name__)

MIN_SAMPLES_TO_TRAIN = 50   # don't train until we have at least 50 labeled candidates
                             # (configurable — with fewer, classifiers will overfit badly)

def build_candidate_dataset(db_path: str, min_samples: int = 30) -> Dict[str, Any]:
    """
    Joins:
      evolution_candidates ec
      JOIN evolution_runs er ON ec.evolution_run_id = er.id
      LEFT JOIN improvement_history ih ON ec.improvement_history_id = ih.id
      LEFT JOIN hypotheses h ON er.hypothesis_id = h.id
      LEFT JOIN failure_patterns fp ON (ec.root_cause_category and er.tool_name)

    Label: ec.sandbox_passed AND ih.result = 'deployed'
      -> 1 if candidate passed sandbox AND was deployed (the winner that stuck)
      -> 0 if disqualified, sandbox_failed, rejected, or rolled_back
      -> EXCLUDE: ih.result = 'pending_review' (label unknown — don't train on ambiguity)

    For each row, compute ALL_FEATURES via compute_feature_vector(candidate_row, db_path)
    from features.py (see Day 40 for this function).
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    try:
        # Check if evolution_candidates table exists (if not, return empty dataset)
        cursor.execute("SELECT 1 FROM evolution_candidates LIMIT 1")
    except sqlite3.OperationalError:
        # Empty DB or table doesn't exist
        return {
            "X": [],
            "y": [],
            "feature_names": ALL_FEATURES,
            "sample_count": 0,
            "positive_rate": 0.0,
            "excluded_pending": 0,
            "tool_coverage": []
        }

    # Execute join across evolution candidates, runs, and history
    query = """
        SELECT 
            ec.*, 
            er.tool_name, 
            ih.result as ih_result
        FROM evolution_candidates ec
        JOIN evolution_runs er ON ec.evolution_run_id = er.id
        LEFT JOIN improvement_history ih ON ec.improvement_history_id = ih.id
    """
    
    rows = cursor.execute(query).fetchall()
    
    X = []
    y = []
    excluded_pending = 0
    tools_seen = set()

    for row in rows:
        row_dict = dict(row)
        
        # Check exclusion criteria
        ih_result = row_dict.get('ih_result')
        if ih_result == 'pending_review':
            excluded_pending += 1
            continue
            
        # Determine label: 1 if sandbox_passed AND deployed, else 0
        sandbox_passed = bool(row_dict.get('sandbox_passed', 0))
        label = 1 if (sandbox_passed and ih_result == 'deployed') else 0
        
        # Compute feature vector
        feature_vector, _ = compute_feature_vector(row_dict, row_dict, db_path)
        
        X.append(feature_vector)
        y.append(label)
        tools_seen.add(row_dict.get('tool_name'))

    conn.close()

    sample_count = len(y)
    positive_rate = sum(y) / sample_count if sample_count > 0 else 0.0

    if sample_count > 0 and positive_rate < 0.15:
        logger.warning(f"Positive rate is low ({positive_rate:.2f}). Training might produce a trivial classifier.")

    return {
        "X": X,
        "y": y,
        "feature_names": ALL_FEATURES,
        "sample_count": sample_count,
        "positive_rate": positive_rate,
        "excluded_pending": excluded_pending,
        "tool_coverage": list(tools_seen)
    }

def build_failure_dataset(db_path: str) -> Dict[str, Any]:
    """
    Unit of analysis: one improvement CYCLE (evolution_runs row), not one candidate.
    Label: did this cycle produce at least one deployed fix?
      -> 1 if evolution_runs has a non-null winner_candidate_id AND the linked
         improvement_history.result = 'deployed'
      -> 0 if run_status='completed' with no deployment, or 'aborted'
      -> EXCLUDE: run_status='running' or 'failed' (infrastructure failure, not
         a meaningful "ARIA tried and failed" signal)

    Returns dataset dict in same structure as build_candidate_dataset.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = """
        SELECT 
            er.*,
            ih.result as ih_result
        FROM evolution_runs er
        LEFT JOIN evolution_candidates ec ON er.winner_candidate_id = ec.id
        LEFT JOIN improvement_history ih ON ec.improvement_history_id = ih.id
        WHERE er.run_status IN ('completed', 'aborted')
        ORDER BY er.started_at ASC
    """
    
    try:
        rows = cursor.execute(query).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not fetch evolution_runs for failure dataset: {e}")
        rows = []
        
    X = []
    y = []
    
    for row in rows:
        row_dict = dict(row)
        
        # Determine label
        # 1 if winner_candidate_id is non-null AND ih.result == 'deployed'
        winner_id = row_dict.get('winner_candidate_id')
        ih_result = row_dict.get('ih_result')
        
        label = 1 if (winner_id and ih_result == 'deployed') else 0
        
        feature_vector = compute_cycle_feature_vector(row_dict, db_path)
        X.append(feature_vector)
        y.append(label)
        
    conn.close()
    
    sample_count = len(y)
    class_1_count = sum(y)
    class_0_count = sample_count - class_1_count
    
    return {
        "X": X,
        "y": y,
        "feature_names": list(FAILURE_FEATURES),
        "sample_count": sample_count,
        "class_balance": {
            "0 (Failure/Aborted)": class_0_count,
            "1 (Deployed)": class_1_count
        }
    }

POST_DEPLOY_MONITOR_WINDOW_DAYS = 7

def build_risk_dataset(db_path: str) -> Dict[str, Any]:
    """
    Unit of analysis: improvement_history rows WHERE result IN ('deployed', 'rolled_back').
    """
    import time
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cutoff_time = time.time() - (POST_DEPLOY_MONITOR_WINDOW_DAYS * 86400)
    
    query = """
        SELECT 
            ih.id as ih_id,
            ih.timestamp as ih_timestamp,
            ih.result as ih_result,
            ih.fix_code_hash,
            ec.*,
            er.tool_name,
            er.hypothesis_id
        FROM improvement_history ih
        LEFT JOIN evolution_candidates ec ON ih.id = ec.improvement_history_id
        LEFT JOIN evolution_runs er ON ec.evolution_run_id = er.id
        WHERE ih.result IN ('deployed', 'rolled_back')
          AND ih.timestamp < ?
        GROUP BY ih.id
        ORDER BY ih.timestamp ASC
    """
    
    try:
        rows = cursor.execute(query, (cutoff_time,)).fetchall()
    except sqlite3.OperationalError as e:
        logger.warning(f"Could not fetch improvement_history for risk dataset: {e}")
        rows = []
        
    X = []
    y = []
    
    for row in rows:
        row_dict = dict(row)
        
        ih_result = row_dict.get('ih_result')
        fix_code_hash = row_dict.get('fix_code_hash')
        ih_timestamp = row_dict.get('ih_timestamp')
        
        # Determine label
        # 1 if result = 'rolled_back' OR (result='deployed' AND subsequent rolled_back exists for same hash)
        if ih_result == 'rolled_back':
            label = 1
        else:
            # Check for subsequent rollback
            subsequent_rollback = False
            if fix_code_hash:
                try:
                    check = cursor.execute(
                        "SELECT 1 FROM improvement_history WHERE fix_code_hash = ? AND result = 'rolled_back' AND timestamp > ?",
                        (fix_code_hash, ih_timestamp)
                    ).fetchone()
                    if check:
                        subsequent_rollback = True
                except sqlite3.OperationalError:
                    pass
            label = 1 if subsequent_rollback else 0
            
        # rank_1_margin calculation
        run_id = row_dict.get('evolution_run_id')
        margin = 0.0
        if run_id:
            try:
                cands = cursor.execute(
                    "SELECT composite_score FROM evolution_candidates WHERE evolution_run_id = ? AND (disqualified = 0 OR disqualified IS NULL) ORDER BY composite_score DESC LIMIT 2",
                    (run_id,)
                ).fetchall()
                if len(cands) >= 2:
                    c1 = dict(cands[0]).get('composite_score', 0)
                    c2 = dict(cands[1]).get('composite_score', 0)
                    if c1 is not None and c2 is not None:
                        margin = float(c1) - float(c2)
            except sqlite3.OperationalError:
                pass
        
        row_dict["rank_1_margin"] = margin
        
        feature_vector = compute_risk_feature_vector(row_dict, db_path)
        X.append(feature_vector)
        y.append(label)
        
    conn.close()
    
    sample_count = len(y)
    class_1_count = sum(y)
    class_0_count = sample_count - class_1_count
    
    if class_1_count == 0:
        logger.warning("No rollbacks exist yet in the dataset. The risk predictor may not be trainable until later Phase 4 data accumulates.")
        
    return {
        "X": X,
        "y": y,
        "feature_names": list(RISK_FEATURES),
        "sample_count": sample_count,
        "class_balance": {
            "0 (Safe)": class_0_count,
            "1 (Rolled Back)": class_1_count
        }
    }

