"""
aria/evolution/ranking.py
─────────────────────────
Multi-dimensional fitness ranking of candidates based on test pass rate, latency,
rule compliance, and strategic diversity.
"""

from __future__ import annotations

import re
from typing import Any

from aria.metrics.db import get_connection

# Weights — all tunable in Day 31-38; document defaults in aria/evolution/README.md
FITNESS_WEIGHTS = {
    "test_pass_rate":       0.50,   # primary — does it pass the test suite?
    "latency_improvement":  0.25,   # did p90 get better, worse, or same vs baseline?
    "rule_compliance":      0.15,   # Phase 3 integration
    "exploration_bonus":    0.10,   # small bonus for a non-dominant strategy (diversity pressure)
}

def extract_keywords(rule_text: str) -> list[str]:
    """
    Simple heuristic keyword extraction for rule compliance.
    """
    # Exclude common stop words and punctuation
    words = re.findall(r'\b[a-z]{4,}\b', rule_text.lower())
    stop_words = {"this", "that", "with", "from", "your", "have", "must", "should", "always", "never", "only", "code", "function", "class", "method", "variable", "when", "then", "else"}
    return [w for w in words if w not in stop_words]

def compute_rule_compliance_score(candidate: dict, active_rules: list[dict]) -> float:
    """
    For each active engineering rule in the category matching this cycle:
      - Run the rule's text against the candidate's source_code via a cheap
        heuristic check.
    Returns fraction of applicable rules that the candidate appears to comply with.
    A candidate with no applicable active rules gets compliance_score = 1.0 (neutral).
    """
    if not active_rules:
        return 1.0

    matched = 0
    code = candidate.get("source_code", "").lower()
    
    for rule in active_rules:
        rule_text = rule.get("rule_text", "")
        keywords = extract_keywords(rule_text)
        
        # If no keywords extracted, we assume it complies rather than penalize
        if not keywords:
            matched += 1
            continue
            
        # Check if any keyword from the rule is in the code
        if any(kw in code for kw in keywords):
            matched += 1
            
    return float(matched) / len(active_rules)

def exploration_bonus(strategy: str, strategy_frequency: dict[str, int]) -> float:
    """
    A small signal to prevent premature convergence on one strategy.
    """
    min_freq = min(strategy_frequency.values()) if strategy_frequency else 0
    this_freq = strategy_frequency.get(strategy, 0)
    
    if this_freq <= min_freq:
        return 1.0
    else:
        return max(0.0, 1.0 - (this_freq - min_freq) / 10.0)

def compute_composite_score(candidate: dict, active_rules: list[dict], strategy_frequency: dict[str, int]) -> float:
    """
    Computes a composite score [0.0, 1.0] weighing multiple fitness dimensions.
    """
    # 1. Test Pass Rate
    test_pass_rate = candidate.get("test_pass_rate", 0.0)
    
    # 2. Latency Improvement
    baseline_p90 = candidate.get("baseline_fitness", 0.0)  # Wait, baseline_fitness is overall_score?
    # Actually, from arena.py, candidate has `baseline_fitness` (score) and `p90_latency_ms`.
    # But wait, arena.py extracts `p90_latency_ms` and `baseline_fitness`.
    # Let's extract latency from combat_report if available.
    combat_report = candidate.get("combat_report") or {}
    baseline_p90 = 0.0
    candidate_p90 = candidate.get("p90_latency_ms", 0.0)
    
    if combat_report and "baseline" in combat_report:
        baseline_p90 = combat_report["baseline"].get("latency_p90", 0.0) * 1000.0
        
    if baseline_p90 > 0:
        latency_diff = (baseline_p90 - candidate_p90) / baseline_p90
        latency_improvement = max(-1.0, min(1.0, latency_diff))
    else:
        latency_improvement = 0.0
        
    normalized_latency = 0.5 + 0.5 * latency_improvement
    
    # 3. Rule Compliance
    rule_compliance = compute_rule_compliance_score(candidate, active_rules)
    
    # 4. Exploration Bonus
    strategy = candidate.get("strategy", "")
    if hasattr(strategy, "value"):
        strategy = strategy.value
    bonus = exploration_bonus(strategy, strategy_frequency)
    
    composite = sum(weight * score for weight, score in zip(
        [
            FITNESS_WEIGHTS["test_pass_rate"],
            FITNESS_WEIGHTS["latency_improvement"],
            FITNESS_WEIGHTS["rule_compliance"],
            FITNESS_WEIGHTS["exploration_bonus"]
        ],
        [
            test_pass_rate,
            normalized_latency,
            rule_compliance,
            bonus
        ]
    ))
    
    # Ensure it's strictly [0.0, 1.0] due to float math
    return max(0.0, min(1.0, composite))

def rank_candidates(
    candidates: list[dict], 
    db_path: str, 
    evolution_run_id: int, 
    active_rules: list[dict], 
    strategy_frequency: dict[str, int]
) -> list[dict]:
    """
    1. Compute composite_score for each non-disqualified candidate.
    2. Sort descending by composite_score; assign rank 1, 2, 3... (disqualified = NULL rank).
    3. UPDATE evolution_candidates with rule_compliance_score, composite_score, rank.
    4. UPDATE evolution_runs SET winner_candidate_id = <rank-1 id>.
    Returns sorted list (rank 1 first).
    """
    
    for c in candidates:
        if c.get("disqualified") == 1:
            c["rule_compliance_score"] = None
            c["composite_score"] = None
            c["rank"] = None
            continue
            
        c["rule_compliance_score"] = compute_rule_compliance_score(c, active_rules)
        c["composite_score"] = compute_composite_score(c, active_rules, strategy_frequency)
        
    # Sort valid candidates
    valid_candidates = [c for c in candidates if c.get("disqualified") == 0]
    valid_candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    
    for idx, c in enumerate(valid_candidates):
        c["rank"] = idx + 1
        
    # Return all candidates (ranked ones first, then disqualified ones)
    disqualified = [c for c in candidates if c.get("disqualified") == 1]
    sorted_candidates = valid_candidates + disqualified
    
    # Update DB
    with get_connection() as conn:
        for c in sorted_candidates:
            if "id" not in c:
                continue
            conn.execute(
                """
                UPDATE evolution_candidates 
                SET rule_compliance_score = ?, composite_score = ?, rank = ?
                WHERE id = ?
                """,
                (c.get("rule_compliance_score"), c.get("composite_score"), c.get("rank"), c["id"])
            )
            
        if valid_candidates and "id" in valid_candidates[0]:
            conn.execute(
                """
                UPDATE evolution_runs
                SET winner_candidate_id = ?
                WHERE id = ?
                """,
                (valid_candidates[0]["id"], evolution_run_id)
            )
            
    return sorted_candidates
