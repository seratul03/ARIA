import math
import time
from aria.metrics.db import get_connection
from aria.memory.store import update_derived_stats

HALF_LIFE_DAYS = 30  # tune later

def compute_memory_score(
    occurrence_count: int, 
    success_ratio: float | None,
    last_seen_ts: float, 
    weights=(0.4, 0.3, 0.3)
) -> float:
    w_success, w_freq, w_recency = weights

    success_component = success_ratio if success_ratio is not None else 0.5  # neutral prior
    freq_component = min(math.log1p(occurrence_count) / math.log1p(50), 1.0)  # cap at ~50 occurrences
    
    # Protect against missing/invalid timestamps
    if not last_seen_ts:
        last_seen_ts = time.time()
        
    age_days = max((time.time() - last_seen_ts) / 86400, 0.0)
    recency_component = 0.5 ** (age_days / HALF_LIFE_DAYS)

    return (
        w_success * success_component
        + w_freq * freq_component
        + w_recency * recency_component
    )

def recompute_all_scores(db_path: str = "") -> None:
    """Recompute memory_score for every row. Idempotent. Safe to run repeatedly."""
    
    # 1. Update failure_history
    with get_connection() as conn:
        # Get all failure history rows
        failures = conn.execute("SELECT id, occurrence_count, last_seen, timestamp FROM failure_history").fetchall()
        failures = [dict(r) for r in failures]
        
    for fail in failures:
        row_id = fail["id"]
        occurrence_count = fail.get("occurrence_count") or 1
        last_seen = fail.get("last_seen") or fail.get("timestamp") or time.time()
        
        # Calculate success ratio for this failure
        # Fraction of linked improvement_history rows with result='deployed'
        with get_connection() as conn:
            fixes = conn.execute(
                "SELECT result FROM improvement_history WHERE triggering_failure_id = ?",
                (row_id,)
            ).fetchall()
            
        success_ratio = None
        if fixes:
            deployed = sum(1 for f in fixes if f["result"] == "deployed")
            success_ratio = deployed / len(fixes)
            
        score = compute_memory_score(
            occurrence_count=occurrence_count,
            success_ratio=success_ratio,
            last_seen_ts=last_seen
        )
        
        update_derived_stats("failure_history", row_id, {"memory_score": score})

    # 2. Update improvement_history
    with get_connection() as conn:
        fixes = conn.execute("SELECT id, result, timestamp, reuse_count, reuse_success_count FROM improvement_history").fetchall()
        fixes = [dict(r) for r in fixes]
        
    for fix in fixes:
        row_id = fix["id"]
        result = fix["result"]
        timestamp = fix["timestamp"] or time.time()
        reuse_count = fix.get("reuse_count") or 0
        reuse_success_count = fix.get("reuse_success_count") or 0
        
        if result == "deployed":
            if reuse_count == 0:
                success_ratio = 1.0
            else:
                success_ratio = reuse_success_count / reuse_count
        else:
            success_ratio = 0.0 # Rejected/rolled_back fixes are inherently unsuccessful
            
        # For fixes, occurrence_count isn't directly applicable, treat it as reuse_count + 1
        score = compute_memory_score(
            occurrence_count=reuse_count + 1,
            success_ratio=success_ratio,
            last_seen_ts=timestamp
        )
        
        update_derived_stats("improvement_history", row_id, {"memory_score": score})
