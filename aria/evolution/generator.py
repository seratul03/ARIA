"""
aria/evolution/generator.py
────────────────────────────
Implementation of multi-strategy candidate generation (Day 25).
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any

from aria.config import settings
from aria.improvement.engine import ImprovementEngine
from aria.introspection.engine import WeaknessReport

logger = logging.getLogger(__name__)

class GenerationStrategy(str, Enum):
    RULE_GUIDED      = "rule_guided"       # Top-confidence Phase 3 rule(s) for this category as explicit directive
    RETRIEVAL_BASED  = "retrieval_based"   # Phase 1 retrieval only (similar past fixes), no rules — maximally grounded
    STRUCTURAL       = "structural"        # Different code-structural approach: if prior fixes added a wrapper, try rewriting core
    MUTATION         = "mutation"          # Apply mutation (Day 30) to the best prior deployed fix for this tool

STRATEGY_ORDER = [
    GenerationStrategy.RULE_GUIDED,
    GenerationStrategy.RETRIEVAL_BASED,
    GenerationStrategy.STRUCTURAL,
    GenerationStrategy.MUTATION,
]

def infer_structural_alternative_hint(tool_name: str, db_path: str) -> str:
    """
    Queries improvement_history WHERE tool_name=? AND result='deployed'
    to find the dominant structural pattern in prior fixes.
    Returns a short phrase like "added retry wrappers" for injection into
    the STRUCTURAL directive.
    """
    from aria.metrics.db import get_connection

    with get_connection() as conn:
        rows = conn.execute(
            "SELECT fix_summary FROM improvement_history WHERE tool_name = ? AND result = 'deployed'",
            (tool_name,)
        ).fetchall()

    if not rows:
        return "existing approach"

    summaries = [row["fix_summary"].lower() for row in rows if row["fix_summary"]]
    keywords = {
        "retry": "added retry wrappers",
        "wrapper": "added wrappers",
        "validation": "added input validation",
        "caching": "implemented caching",
        "timeout": "added timeout handling",
        "refactor": "refactored core logic"
    }

    # Count occurrences
    counts = {kw: 0 for kw in keywords.keys()}
    for summary in summaries:
        for kw in keywords.keys():
            if kw in summary:
                counts[kw] += 1

    best_kw = max(counts, key=counts.get)
    if counts[best_kw] > 0:
        return keywords[best_kw]
    
    return "existing approach"


def get_best_prior_fix_summary(tool_name: str, db_path: str) -> str:
    """
    Finds the best prior deployed fix for this tool by fitness_delta.
    """
    from aria.metrics.db import get_connection

    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT fix_summary, fitness_delta 
            FROM improvement_history 
            WHERE tool_name = ? AND result = 'deployed'
            ORDER BY fitness_delta DESC LIMIT 1
            """,
            (tool_name,)
        ).fetchone()

    if row and row["fix_summary"]:
        return row["fix_summary"]
    return "Unknown best fix"


def generate_candidates(
    evolution_run_id: int,
    tool_name: str,
    weakness_context: dict,
    hypothesis: dict | None,
    db_path: str,
    strategies: list[GenerationStrategy] | None = None,
) -> list[dict]:
    """
    For each strategy in strategies:
      1. Build a strategy-specific prompt via prompt_builder.
      2. Make one LLM call.
      3. Parse the returned code + fix_summary.
      4. On parse error: log, do NOT retry.
    Returns a list of {strategy, source_code, fix_summary, prompt_tokens_used}.
    """
    if strategies is None:
        strategies = STRATEGY_ORDER

    candidates = []
    engine = ImprovementEngine()

    for strategy in strategies:
        logger.info(f"Generating candidate using strategy: {strategy.value}")
        
        # We need a WeaknessReport from the context
        report = weakness_context.get("report")
        if not report:
            continue

        # Attach the strategy to the report temporarily so prompt_builder can see it
        # Or better, we can pass it to the engine, but we want to avoid changing engine's signature too much.
        # Let's pass strategy into engine.generate_improvement.
        # For that, we will update ImprovementEngine to accept strategy.
        result = engine.generate_improvement(report, cycle_id=None, strategy=strategy)
        
        if result.success and result.generated_code:
            # fix_summary isn't returned by LLM, generate an appropriate one based on strategy
            fix_summary = f"Improved via {strategy.value} strategy"
            
            candidates.append({
                "strategy": strategy,
                "source_code": result.generated_code,
                "fix_summary": fix_summary,
                "prompt_tokens_used": result.tokens_used,
                "pending_rule_app_ids": result.pending_rule_app_ids,
            })
        else:
            logger.warning(f"Strategy {strategy.value} failed: {result.error}")

    return candidates
