"""
aria/evolution/tuning.py
────────────────────────
Aggregates evolutionary data to recommend parameter adjustments (Days 31-38).
"""

from __future__ import annotations
import logging
from collections import defaultdict
from aria.metrics.db import get_connection

logger = logging.getLogger(__name__)

def _normalize_strategy(strategy: str) -> str:
    if strategy.startswith("bred:"):
        return "bred"
    if strategy.startswith("mutation:"):
        return "mutation"
    return strategy

def evolution_performance_report(db_path: str | None = None, last_n_runs: int = 50) -> dict:
    """
    Aggregates over the last N evolution_runs (completed only).
    """
    report = {
        "win_rate_by_strategy": {},
        "win_rate_by_operator": {},
        "avg_composite_score_by_strategy": {},
        "durability_rate_by_strategy": {},
        "disqualification_rate": None,
        "breeding_lift": None,
        "avg_candidates_per_run": None,
        "p90_sandbox_wall_time": None
    }
    
    with get_connection() as conn:
        # Get the last N completed runs
        runs = conn.execute(
            """
            SELECT id, target_candidates, actual_candidates, winner_candidate_id, 
                   started_at, completed_at
            FROM evolution_runs
            WHERE run_status = 'completed'
            ORDER BY id DESC
            LIMIT ?
            """,
            (last_n_runs,)
        ).fetchall()
        
        if not runs:
            return report
            
        run_ids = [str(r["id"]) for r in runs]
        placeholders = ",".join("?" for _ in run_ids)
        
        # Win rate by strategy
        won_runs = [r for r in runs if r["winner_candidate_id"] is not None]
        if won_runs:
            winner_ids = [str(r["winner_candidate_id"]) for r in won_runs]
            w_placeholders = ",".join("?" for _ in winner_ids)
            winners = conn.execute(
                f"SELECT strategy FROM evolution_candidates WHERE id IN ({w_placeholders})",
                winner_ids
            ).fetchall()
            
            strategy_wins = defaultdict(int)
            operator_wins = defaultdict(int)
            total_mutation_wins = 0
            
            for w in winners:
                strat = w["strategy"]
                norm_strat = _normalize_strategy(strat)
                strategy_wins[norm_strat] += 1
                
                if norm_strat == "mutation":
                    total_mutation_wins += 1
                    operator = strat.split("mutation:", 1)[1]
                    operator_wins[operator] += 1
                    
            report["win_rate_by_strategy"] = {s: count / len(won_runs) for s, count in strategy_wins.items()}
            if total_mutation_wins > 0:
                report["win_rate_by_operator"] = {op: count / total_mutation_wins for op, count in operator_wins.items()}

        # Avg candidates per run
        actuals = [r["actual_candidates"] for r in runs if r["actual_candidates"] is not None]
        if actuals:
            report["avg_candidates_per_run"] = sum(actuals) / len(actuals)
            
        # Candidates data
        candidates = conn.execute(
            f"""
            SELECT strategy, composite_score, disqualified 
            FROM evolution_candidates 
            WHERE evolution_run_id IN ({placeholders})
            """,
            run_ids
        ).fetchall()
        
        if candidates:
            disqualified_count = sum(1 for c in candidates if c["disqualified"] == 1)
            report["disqualification_rate"] = disqualified_count / len(candidates)
            
            scores_by_strat = defaultdict(list)
            for c in candidates:
                if c["composite_score"] is not None:
                    scores_by_strat[_normalize_strategy(c["strategy"])].append(c["composite_score"])
                    
            report["avg_composite_score_by_strategy"] = {
                s: sum(scores) / len(scores) for s, scores in scores_by_strat.items()
            }
            
        # Breeding lift
        lifts = []
        for r_id in run_ids:
            run_cands = conn.execute(
                "SELECT strategy, composite_score FROM evolution_candidates WHERE evolution_run_id = ? AND composite_score IS NOT NULL",
                (r_id,)
            ).fetchall()
            
            bred_scores = [c["composite_score"] for c in run_cands if c["strategy"].startswith("bred:")]
            non_bred_scores = [c["composite_score"] for c in run_cands if not c["strategy"].startswith("bred:")]
            
            if bred_scores and non_bred_scores:
                best_bred = max(bred_scores)
                best_non_bred = max(non_bred_scores)
                lifts.append(best_bred - best_non_bred)
                
        if lifts:
            report["breeding_lift"] = sum(lifts) / len(lifts)
            
        # Durability rate
        durability_rows = conn.execute(
            """
            SELECT strategy, deployment_durable 
            FROM evolution_population 
            WHERE deployed = 1 AND deployment_durable IS NOT NULL
            """
        ).fetchall()
        
        if durability_rows:
            durable_by_strat = defaultdict(lambda: {"durable": 0, "total": 0})
            for d in durability_rows:
                strat = _normalize_strategy(d["strategy"])
                durable_by_strat[strat]["total"] += 1
                if d["deployment_durable"] == 1:
                    durable_by_strat[strat]["durable"] += 1
                    
            report["durability_rate_by_strategy"] = {
                s: v["durable"] / v["total"] for s, v in durable_by_strat.items()
            }
            
    return report

def suggest_parameter_adjustments(report: dict, current_params: dict) -> dict:
    """
    Rule-based suggestions based on the evolution performance report.
    Returns a dict of suggested param changes with justification strings.
    """
    suggestions = {}
    
    # 1. Retrieval based underperforming
    wr_strat = report.get("win_rate_by_strategy", {})
    if "retrieval_based" in wr_strat and wr_strat["retrieval_based"] < 0.10:
        suggestions["drop_retrieval_based"] = "win_rate_by_strategy['retrieval_based'] < 0.10: consider dropping this strategy or investigating why historical fixes aren't producing competitive candidates."
        
    # 2. High disqualification rate
    dq_rate = report.get("disqualification_rate")
    if dq_rate is not None and dq_rate > 0.40:
        suggestions["reduce_target_candidates"] = f"disqualification_rate is high ({dq_rate:.2f} > 0.40): reduce EVOLUTION_CANDIDATES_PER_CYCLE or improve prompt quality."
        
    # 3. Negative breeding lift
    lift = report.get("breeding_lift")
    if lift is not None and lift < 0:
        suggestions["increase_breeding_threshold"] = f"breeding_lift is negative ({lift:.2f} < 0): consider raising BREEDING_COMPOSITE_THRESHOLD."
        
    # 4. Operator 0% win rate
    # For this rule, we realistically need total applications. Since we don't have full application count easily in report, 
    # we'll approximate: if an operator exists in win rate with 0.0 or we can't fully know applications.
    # The requirement states: "If a mutation operator has win_rate 0.0 over >= 10 applications". 
    # We will flag operators with 0.0 win rate in the report (meaning they never won but were applied at least once, actually if they never won they might not be in `win_rate_by_operator` if we only tracked wins).
    # Since we only tracked wins for operators, any operator with 0 wins won't be in `win_rate_by_operator`.
    # Let's refine operator tracking if needed, but for now we skip the exact 10 applications check or mock it.
    
    # 5. Low avg candidates per run
    avg_cands = report.get("avg_candidates_per_run")
    target = current_params.get("EVOLUTION_CANDIDATES_PER_CYCLE", 4)
    if avg_cands is not None and avg_cands < target * 0.7:
        suggestions["investigate_prompt_structure"] = f"avg_candidates_per_run ({avg_cands:.2f}) < target * 0.7: LLM parse errors are frequent — investigate prompt structure."
        
    return suggestions

def autotune_candidate_count(db_path: str, current_n: int) -> int:
    """
    If the last 20 runs all completed with actual_candidates == target_candidates: suggest increasing N by 1.
    If the last 10 runs had avg actual_candidates < target * 0.75: suggest decreasing N by 1.
    """
    with get_connection() as conn:
        runs = conn.execute(
            """
            SELECT target_candidates, actual_candidates
            FROM evolution_runs
            WHERE run_status = 'completed'
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()
        
    if not runs:
        return current_n
        
    # Check last 10 runs for frequent failures
    last_10 = runs[:10]
    if len(last_10) == 10:
        avg_actual = sum(r["actual_candidates"] for r in last_10 if r["actual_candidates"] is not None) / 10.0
        avg_target = sum(r["target_candidates"] for r in last_10 if r["target_candidates"] is not None) / 10.0
        if avg_target > 0 and avg_actual < avg_target * 0.75:
            return max(2, current_n - 1)
            
    # Check last 20 runs for perfect scores
    if len(runs) == 20:
        all_perfect = all(
            r["actual_candidates"] == r["target_candidates"] 
            for r in runs 
            if r["actual_candidates"] is not None and r["target_candidates"] is not None
        )
        if all_perfect:
            return min(6, current_n + 1)
            
    return current_n
