"""
aria/introspection/wtds.py
──────────────────────────
Implements the Weighted Temporal Degradation Score (WTDS) algorithm.
Prioritizes tools for improvement based on Health, Trajectory, Resistance, Impact, and Recency.
"""

import math
from aria.metrics.db import get_connection, get_tool_stats

def _get_pass_rate_last_n(tool_name: str, n: int, offset: int = 0) -> float | None:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT success FROM tool_executions WHERE tool_name = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (tool_name, n, offset)
        ).fetchall()
        
        if not rows:
            return None
        return sum(r["success"] for r in rows) / len(rows)

def _get_total_executions_all_tools() -> int:
    with get_connection() as conn:
        row = conn.execute("SELECT COUNT(*) as c FROM tool_executions").fetchone()
        return row["c"] if row else 0

def _count_improvements(tool_name: str) -> tuple[int, int]:
    """Returns (failed_attempts, successful_deploys)"""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT result FROM improvement_history WHERE tool_name = ?",
            (tool_name,)
        ).fetchall()
        
        failed = sum(1 for r in rows if r["result"] in ("rejected", "failed"))
        success = sum(1 for r in rows if r["result"] == "deployed")
        return failed, success

# _get_hours_since_last_failure removed in favor of OWS Stagnation.

def compute_wtds(tool_name: str) -> dict:
    """
    Computes WTDS. Returns dict with 'wtds' score and 'dominant_factor'.
    """
    stats = get_tool_stats(tool_name, window=100)
    if not stats or stats.total_executions == 0:
        return {"wtds": 0.0, "dominant_factor": "none", "components": {}}
        
    # --- COMPONENT 1: Current Health (30%) ---
    pass_rate = stats.success_rate
    p90_latency = stats.p90_latency
    memory_usage = stats.avg_memory_mb
    token_cost = stats.avg_tokens_used

    health_score = (
        0.50 * (1.0 - pass_rate) +
        0.25 * min(p90_latency / 10.0, 1.0) +
        0.15 * min(memory_usage / 512.0, 1.0) +
        0.10 * min(token_cost / 4000.0, 1.0)
    )

    # --- COMPONENT 2: Trajectory (25%) ---
    recent_pass_rate = _get_pass_rate_last_n(tool_name, 10, 0)
    previous_pass_rate = _get_pass_rate_last_n(tool_name, 10, 10)
    
    trajectory_score = 0.0
    if stats.total_executions >= 20 and recent_pass_rate is not None and previous_pass_rate is not None:
        degradation = previous_pass_rate - recent_pass_rate
        trajectory_score = max(min(degradation * 2.0, 1.0), 0.0)

    # --- COMPONENT 3: Fix Resistance (20%) ---
    failed_attempts, successful_deploys = _count_improvements(tool_name)
    total_attempts = failed_attempts + successful_deploys
    
    resistance_score = 0.0
    if total_attempts > 0:
        resistance_ratio = failed_attempts / total_attempts
        resistance_score = min(math.log1p(failed_attempts) / math.log1p(20), 1.0) * resistance_ratio
        
        # CIRCUIT BREAKER: If consistently failing and never succeeding, back off
        if failed_attempts > 10 and successful_deploys == 0:
            resistance_score *= 0.5  # Penalize score so it doesn't infinite loop

    # --- COMPONENT 4: System Impact (15%) ---
    all_executions = _get_total_executions_all_tools()
    impact_score = 0.0
    if all_executions > 0:
        usage_fraction = stats.total_executions / all_executions
        impact_score = min(usage_fraction * 3.0, 1.0)

    # --- COMPONENT 5: Opportunity-Weighted Stagnation (OWS) (10%) ---
    from aria.metrics.db import get_stagnation_data
    import time
    
    stag_data = get_stagnation_data(tool_name)
    times_bypassed = stag_data["times_bypassed"]
    last_attempt_ts = stag_data["last_attempt_ts"]
    diff_mult = stag_data["difficulty_multiplier"]
    
    # 1. Opportunity
    opportunity = (1.0 - stats.success_rate) ** 2
    
    # 2. Bypass Stagnation
    lambda_bypass = 20.0 / diff_mult
    bypass_stagnation = 1.0 - math.exp(-times_bypassed / lambda_bypass)
    
    # 3. Time Stagnation
    hours_since = (time.time() - last_attempt_ts) / 3600.0
    time_stagnation = 1.0 - math.exp(-hours_since / 48.0)
    
    # 4. Total Stagnation
    total_stagnation = (0.7 * bypass_stagnation) + (0.3 * time_stagnation)
    
    # 5. OWS Score
    ows_score = (0.6 * opportunity) + (0.4 * total_stagnation)

    # --- FINAL COMPOSITE ---
    weights = {"health": 0.30, "trajectory": 0.25, "resistance": 0.20, "impact": 0.15, "ows_stagnation": 0.10}
    
    components = {
        "health": health_score,
        "trajectory": trajectory_score,
        "resistance": resistance_score,
        "impact": impact_score,
        "ows_stagnation": ows_score
    }
    
    wtds = sum(weights[k] * components[k] for k in weights)
    
    # Calculate dominant factor (weighted contribution)
    contributions = {k: weights[k] * components[k] for k in weights}
    dominant_factor = max(contributions.items(), key=lambda x: x[1])[0] if wtds > 0 else "none"
    
    return {
        "wtds": round(wtds, 4),
        "dominant_factor": dominant_factor,
        "components": components,
        "rank": 0 # Filled in later
    }

def get_all_wtds_scores() -> dict[str, dict]:
    """Returns a dict mapping tool_name to its WTDS score breakdown."""
    from aria.metrics.db import get_all_tool_stats
    stats_list = get_all_tool_stats(window=100)
    
    scores = {}
    for stats in stats_list:
        scores[stats.tool_name] = compute_wtds(stats.tool_name)
        
    # Sort and assign ranks
    ranked = sorted(scores.items(), key=lambda x: x[1]["wtds"], reverse=True)
    for i, (tool, score_dict) in enumerate(ranked):
        score_dict["rank"] = i + 1
        
    return scores
