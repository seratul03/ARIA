import sqlite3
import logging
from typing import Dict, Any
from aria.predictors.features import ALL_FEATURES, compute_feature_vector

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
        feature_vector = compute_feature_vector(row_dict, db_path)
        
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
