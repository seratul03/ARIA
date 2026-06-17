"""
aria/evolution/mutation.py
──────────────────────────
Applies focused, engineering-level perturbations to the strongest candidates.
"""

from __future__ import annotations
import logging
import random
from enum import Enum

from aria.metrics.db import get_connection
from aria.evolution.population import get_population_for_breeding
from aria.evolution.breeding import extract_core_diff
from aria.config import settings
from aria.core.rate_limiter import groq_limiter

logger = logging.getLogger(__name__)

class MutationOperator(str, Enum):
    TIGHTEN_VALIDATION   = "tighten_validation"    # Add/strengthen an input check
    LOOSEN_VALIDATION    = "loosen_validation"     # Remove an overly-strict check that caused failures
    ADD_RETRY            = "add_retry"             # Add retry logic (if not already present)
    TUNE_RETRY           = "tune_retry"            # Change retry params (count, backoff factor, jitter)
    SWAP_EXCEPTION_SCOPE = "swap_exception_scope"  # Catch a broader/narrower exception type
    ADD_CACHING          = "add_caching"           # Introduce caching for expensive/repeated operations
    REMOVE_CACHING       = "remove_caching"        # Remove caching that's causing stale-data failures
    EXTRACT_HELPER       = "extract_helper"        # Refactor complex logic into a helper function
    INLINE_HELPER        = "inline_helper"         # Inline a helper that's creating abstraction confusion
    ADJUST_TIMEOUT       = "adjust_timeout"        # Change a timeout value (increase or decrease)

CATEGORY_MUTATION_AFFINITIES: dict[str, list[MutationOperator]] = {
    "Network":      [MutationOperator.ADD_RETRY, MutationOperator.TUNE_RETRY, MutationOperator.ADJUST_TIMEOUT],
    "Validation":   [MutationOperator.TIGHTEN_VALIDATION, MutationOperator.LOOSEN_VALIDATION, MutationOperator.SWAP_EXCEPTION_SCOPE],
    "Performance":  [MutationOperator.ADD_CACHING, MutationOperator.REMOVE_CACHING, MutationOperator.ADJUST_TIMEOUT],
    "Logic":        [MutationOperator.EXTRACT_HELPER, MutationOperator.INLINE_HELPER, MutationOperator.SWAP_EXCEPTION_SCOPE],
    "Concurrency":  [MutationOperator.SWAP_EXCEPTION_SCOPE, MutationOperator.EXTRACT_HELPER],
    "Security":     [MutationOperator.TIGHTEN_VALIDATION, MutationOperator.SWAP_EXCEPTION_SCOPE],
    "Configuration":[MutationOperator.TIGHTEN_VALIDATION, MutationOperator.EXTRACT_HELPER],
}

OPERATOR_DESCRIPTIONS: dict[MutationOperator, str] = {
    MutationOperator.TIGHTEN_VALIDATION: "Add or strengthen input validation. If parameters are assumed valid, add explicit checks.",
    MutationOperator.LOOSEN_VALIDATION: "Remove or loosen overly strict input validation that may be causing false-positive rejections.",
    MutationOperator.ADD_RETRY: "Add retry logic with exponential backoff around the primary failure point. If retry parameters exist already, this is a no-op — do not apply this mutation.",
    MutationOperator.TUNE_RETRY: "Change the retry parameters: adjust the number of attempts, the base delay, the backoff multiplier, or add jitter if absent.",
    MutationOperator.SWAP_EXCEPTION_SCOPE: "Change the scope of a caught exception. Either catch a broader exception type or narrow it to a more specific subclass.",
    MutationOperator.ADD_CACHING: "Introduce caching (e.g. memoization or a simple LRU cache) for expensive or repeated operations.",
    MutationOperator.REMOVE_CACHING: "Remove caching that might be causing stale-data failures or memory leaks.",
    MutationOperator.EXTRACT_HELPER: "Refactor complex inline logic into a distinct, well-named helper function.",
    MutationOperator.INLINE_HELPER: "Inline a helper function that is causing unnecessary abstraction or confusion.",
    MutationOperator.ADJUST_TIMEOUT: "Change a network or lock timeout value. Either increase it to allow slow operations to complete, or decrease it to fail fast.",
}

def _call_llm(prompt: str) -> str | None:
    try:
        from groq import Groq
    except ImportError:
        logger.error("Groq not installed.")
        return None
        
    try:
        groq_limiter.acquire()
        client = Groq(api_key=settings.groq_api_key)
        response = client.chat.completions.create(
            model=settings.groq_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4, # Lower temperature for focused edits
            max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Mutation LLM call failed: {e}")
        return None

def get_mutation_history(tool_name: str, db_path: str) -> list[str]:
    """
    Returns a list of mutation operators that have been applied previously 
    and added to the population.
    """
    history = []
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT strategy FROM evolution_population WHERE tool_name = ? AND strategy LIKE 'mutation:%'",
            (tool_name,)
        ).fetchall()
        for r in rows:
            op_str = r["strategy"].split("mutation:", 1)[1]
            history.append(op_str)
    return history

def select_mutation_operator(category: str | None, applied_operators_history: list[str]) -> MutationOperator:
    """
    Selects a mutation operator based on category affinity, filtering out
    those that were recently applied.
    """
    if category and category in CATEGORY_MUTATION_AFFINITIES:
        candidates = CATEGORY_MUTATION_AFFINITIES[category]
    else:
        candidates = list(MutationOperator)
        
    # Filter out previously applied operators
    available = [op for op in candidates if op.value not in applied_operators_history]
    
    if not available:
        # If all candidates for this category have been applied, fallback to the full category list
        available = candidates
        
    if not available: # Fallback in case of empty list
        available = list(MutationOperator)
        
    return random.choice(available)

def generate_mutated_candidate(
    base_candidate: dict,
    operator: MutationOperator,
    tool_name: str,
    weakness_context: dict,
    db_path: str,
) -> dict | None:
    """
    Applies EXACTLY ONE structural mutation to the best candidate.
    """
    core_diff = extract_core_diff(base_candidate["candidate_id"], db_path, tool_name)
    
    prompt = f"""You are ARIA, an elite autonomous AI software engineer.
Your task is to perturb the tool `{tool_name}` slightly.

## Weakness Context
{weakness_context.get('problem_description', '')}

## GENERATION DIRECTIVE: Mutation — {operator.value}
Starting from the following existing fix implementation:

Summary: {base_candidate.get('fix_summary')}
Key Original Additions:
```python
{core_diff}
```

Apply EXACTLY ONE change: {OPERATOR_DESCRIPTIONS[operator]}

Do not redesign the overall approach. Do not fix other issues you notice.
Make the single mutation described above and nothing else.
If the mutation description states "if X exists already, this is a no-op", and X exists, you MUST return exactly the string: "NO_OP".

Return ONLY valid Python code for the entire file. No markdown formatting (except for NO_OP).
"""
    
    response = _call_llm(prompt)
    if not response:
        return None
        
    source_code = response.strip()
    if source_code == "NO_OP":
        logger.info(f"Mutation {operator.value} resulted in NO_OP. Aborting mutation.")
        return None
        
    if source_code.startswith("```python"):
        source_code = source_code[9:]
    if source_code.startswith("```"):
        source_code = source_code[3:]
    if source_code.endswith("```"):
        source_code = source_code[:-3]
        
    return {
        "strategy": f"mutation:{operator.value}",
        "source_code": source_code.strip(),
        "fix_summary": f"Mutated ({operator.value}): {base_candidate.get('fix_summary')}",
        "pending_rule_app_ids": [],
        "prompt_tokens_used": 0
    }

def try_mutation_candidate(
    evolution_run_id: int,
    tool_name: str,
    hypothesis: dict | None,
    weakness_context: dict,
    db_path: str,
) -> dict | None:
    """
    Selects base candidate, chooses mutation operator, and synthesizes candidate.
    """
    category = hypothesis.get("category") if hypothesis else None
    population = get_population_for_breeding(tool_name, category, top_k=10, db_path=db_path)
    
    # Prioritize deployment_durable = 1
    base_for_mutation = None
    if population:
        durable_population = [p for p in population if p.get("deployment_durable") == 1]
        if durable_population:
            base_for_mutation = durable_population[0]
        else:
            base_for_mutation = population[0]
            
    if not base_for_mutation:
        logger.info(f"No population available for mutating {tool_name}")
        return None
        
    applied_history = get_mutation_history(tool_name, db_path)
    operator = select_mutation_operator(category, applied_history)
    
    logger.info(f"Mutating base candidate {base_for_mutation['candidate_id']} using {operator.value}")
    
    mutated = generate_mutated_candidate(
        base_candidate=base_for_mutation,
        operator=operator,
        tool_name=tool_name,
        weakness_context=weakness_context,
        db_path=db_path
    )
    
    return mutated
