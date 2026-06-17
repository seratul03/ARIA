"""
aria/evolution/population.py
────────────────────────────
Persistent population store to retain high-fitness candidates for future breeding.
"""

from __future__ import annotations
import logging
from aria.metrics.db import get_connection

logger = logging.getLogger(__name__)

POPULATION_MAX_PER_TOOL = 20     # keep the top 20 candidates per tool
POPULATION_MIN_COMPOSITE = 0.55  # don't store weak candidates

def update_population_after_run(evolution_run_id: int, db_path: str, deployed_candidate_id: int | None = None, root_cause_category: str | None = None) -> dict:
    """
    After a run completes and winner is determined:
    1. For every non-disqualified candidate in this run with composite_score >= POPULATION_MIN_COMPOSITE:
         - INSERT into evolution_population.
         - If candidate was the winner AND gets deployed: set deployed=1.
    2. For the tool, if COUNT(*) > POPULATION_MAX_PER_TOOL in evolution_population:
         - DELETE the lowest-composite-score rows to bring count back to max.
    Returns {"inserted": N, "evicted": M}
    """
    inserted = 0
    evicted = 0
    
    with get_connection() as conn:
        # Fetch valid candidates from this run
        candidates = conn.execute(
            """
            SELECT id, strategy, fix_summary, composite_score, fitness_delta
            FROM evolution_candidates
            WHERE evolution_run_id = ? AND composite_score >= ? AND disqualified = 0
            """,
            (evolution_run_id, POPULATION_MIN_COMPOSITE)
        ).fetchall()
        
        if not candidates:
            return {"inserted": 0, "evicted": 0}
            
        tool_name = conn.execute(
            "SELECT tool_name FROM evolution_runs WHERE id = ?",
            (evolution_run_id,)
        ).fetchone()["tool_name"]
        
        # Insert them
        for c in candidates:
            deployed = 1 if c["id"] == deployed_candidate_id else 0
            
            # Need strategy value correctly
            strategy = c["strategy"]
            
            conn.execute(
                """
                INSERT INTO evolution_population (
                    tool_name, root_cause_category, candidate_id, strategy, fix_summary, composite_score, deployed, fitness_delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tool_name, root_cause_category, c["id"], strategy, c["fix_summary"], c["composite_score"], deployed, c["fitness_delta"])
            )
            inserted += 1
            
        # Enforce POPULATION_MAX_PER_TOOL limit
        total_count = conn.execute(
            "SELECT COUNT(*) as c FROM evolution_population WHERE tool_name = ?",
            (tool_name,)
        ).fetchone()["c"]
        
        if total_count > POPULATION_MAX_PER_TOOL:
            excess = total_count - POPULATION_MAX_PER_TOOL
            
            # Find the IDs of the lowest scoring candidates to delete
            to_delete = conn.execute(
                """
                SELECT id FROM evolution_population
                WHERE tool_name = ?
                ORDER BY composite_score ASC, added_at ASC
                LIMIT ?
                """,
                (tool_name, excess)
            ).fetchall()
            
            delete_ids = [row["id"] for row in to_delete]
            
            if delete_ids:
                placeholders = ",".join("?" for _ in delete_ids)
                conn.execute(
                    f"DELETE FROM evolution_population WHERE id IN ({placeholders})",
                    delete_ids
                )
                evicted += len(delete_ids)
                
    return {"inserted": inserted, "evicted": evicted}

def get_population_for_breeding(tool_name: str, category: str | None, top_k: int = 6, db_path: str | None = None) -> list[dict]:
    """
    Returns the top_k candidates by composite_score for this tool (and optionally
    filtered by root_cause_category). Used by Day 29 (breeding) and Day 30 (mutation).
    """
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                """
                SELECT p.*, c.source_code 
                FROM evolution_population p
                JOIN evolution_candidates c ON p.candidate_id = c.id
                WHERE p.tool_name = ? AND p.root_cause_category = ?
                ORDER BY p.composite_score DESC
                LIMIT ?
                """,
                (tool_name, category, top_k)
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT p.*, c.source_code 
                FROM evolution_population p
                JOIN evolution_candidates c ON p.candidate_id = c.id
                WHERE p.tool_name = ?
                ORDER BY p.composite_score DESC
                LIMIT ?
                """,
                (tool_name, top_k)
            ).fetchall()
            
    return [dict(r) for r in rows]

def get_strategy_frequency(tool_name: str, db_path: str | None = None) -> dict[str, int]:
    """
    For Day 27's exploration_bonus: counts how many times each strategy has
    been deployed (deployed=1) for this tool in the population.
    """
    frequencies = {}
    with get_connection() as conn:
        cursor = conn.execute(
            """
            SELECT strategy, COUNT(*) as count
            FROM evolution_population
            WHERE tool_name = ? AND deployed = 1
            GROUP BY strategy
            """,
            (tool_name,)
        )
        for row in cursor.fetchall():
            frequencies[row["strategy"]] = row["count"]
            
    return frequencies
