"""
aria/evolution/breeding.py
──────────────────────────
Synthesizes new candidates by combining successful strategies from the gene pool.
"""

from __future__ import annotations
import logging
import difflib
from pathlib import Path

from aria.metrics.db import get_connection
from aria.evolution.population import get_population_for_breeding
from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.knowledge.applications import select_rules_for_prompt

logger = logging.getLogger(__name__)

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
            temperature=0.7,
            max_tokens=2000
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Breeding LLM call failed: {e}")
        return None

MIN_POPULATION_FOR_BREEDING = 3
BREEDING_COMPOSITE_THRESHOLD = 0.70

def select_parents(tool_name: str, category: str | None, db_path: str) -> tuple[dict, dict] | None:
    """
    Selects two strong candidates to breed.
    Prioritizes diverse strategies to prevent convergence.
    """
    population = get_population_for_breeding(tool_name, category, top_k=10, db_path=db_path)
    
    if len(population) < MIN_POPULATION_FOR_BREEDING:
        logger.info(f"Population too small for breeding ({len(population)} < {MIN_POPULATION_FOR_BREEDING})")
        return None
        
    # Filter for durable candidates with good fitness
    valid_parents = []
    for p in population:
        # Avoid known bad (rolled back) candidates
        if p.get("deployment_durable") == 0:
            continue
        if p.get("composite_score", 0.0) < BREEDING_COMPOSITE_THRESHOLD:
            continue
        valid_parents.append(p)
        
    if len(valid_parents) < 2:
        return None
        
    # Attempt to find two diverse strategies
    parent_a = valid_parents[0]
    parent_b = None
    
    for p in valid_parents[1:]:
        if p.get("strategy") != parent_a.get("strategy"):
            parent_b = p
            break
            
    if not parent_b:
        # Fallback: converged population, take the top 2 regardless of strategy
        parent_b = valid_parents[1]
        logger.warning(f"Population converged! Breeding from same strategy: {parent_a.get('strategy')}")
        
    return (parent_a, parent_b)

def extract_core_diff(candidate_id: int, db_path: str, tool_name: str, max_lines: int = 30) -> str:
    """
    Extracts the core logical additions made by the candidate.
    """
    with get_connection() as conn:
        candidate = conn.execute(
            "SELECT source_code FROM evolution_candidates WHERE id = ?",
            (candidate_id,)
        ).fetchone()
        
    if not candidate:
        return ""
        
    candidate_code = candidate["source_code"]
    
    # Need to find original baseline
    tool_file = Path(f"aria/tools/{tool_name}.py")
    if not tool_file.exists():
        tool_file = Path(f"tools/{tool_name}.py")
        if not tool_file.exists():
            return candidate_code[:500] # fallback
            
    try:
        baseline_code = tool_file.read_text(encoding="utf-8")
    except Exception:
        return candidate_code[:500]
        
    diff = difflib.unified_diff(
        baseline_code.splitlines(),
        candidate_code.splitlines(),
        fromfile='baseline.py',
        tofile='candidate.py',
        lineterm=''
    )
    
    additions = []
    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            additions.append(line)
            
    result = "\n".join(additions[:max_lines])
    if len(additions) > max_lines:
        result += "\n... [truncated for brevity]"
        
    return result

def generate_bred_candidate(
    tool_name: str,
    parent_a: dict,
    parent_b: dict,
    weakness_context: dict,
    db_path: str,
) -> dict | None:
    """
    Constructs the prompt and synthesizes a new candidate using the LLM.
    """
    diff_a = extract_core_diff(parent_a["candidate_id"], db_path, tool_name)
    diff_b = extract_core_diff(parent_b["candidate_id"], db_path, tool_name)
    
    active_rules = select_rules_for_prompt(tool_name, db_path)
    rules_text = ""
    rule_ids = []
    if active_rules:
        rules_text = "## Engineering Principles\n"
        for i, rule in enumerate(active_rules, 1):
            rules_text += f"{i}. {rule['rule_text']}\n"
            rule_ids.append(rule['id'])
            
    prompt = f"""You are ARIA, an elite autonomous AI software engineer.
Your task is to improve the tool `{tool_name}`.

## Weakness Context
{weakness_context.get('problem_description', '')}
Current Success Rate: {weakness_context.get('success_rate', 0.0):.1%}

## GENERATION DIRECTIVE: Bred Combination
You are combining two highly successful approaches from our genetic population:

### Approach A (from a {parent_a.get('strategy')} fix, composite_score {parent_a.get('composite_score', 0):.2f})
Summary: {parent_a.get('fix_summary')}
Key Additions:
```python
{diff_a}
```

### Approach B (from a {parent_b.get('strategy')} fix, composite_score {parent_b.get('composite_score', 0):.2f})
Summary: {parent_b.get('fix_summary')}
Key Additions:
```python
{diff_b}
```

{rules_text}

Generate a new implementation that incorporates the BEST ELEMENTS of BOTH approaches.
Do not simply concatenate them — synthesize a coherent, elegant solution that addresses the weakness context.
Return ONLY valid Python code for the entire file. No markdown formatting.
"""

    response = _call_llm(prompt)
    if not response:
        return None
        
    source_code = response.strip()
    if source_code.startswith("```python"):
        source_code = source_code[9:]
    if source_code.startswith("```"):
        source_code = source_code[3:]
    if source_code.endswith("```"):
        source_code = source_code[:-3]
        
    # Generate fix summary
    summary_prompt = f"Summarize this code fix in one short sentence, focusing on how it combines two previous approaches: {source_code[:1000]}"
    summary_response = _call_llm(summary_prompt)
    fix_summary = summary_response.strip() if summary_response else "Bred candidate from previous approaches."
    
    return {
        "strategy": f"bred:{parent_a['candidate_id']}+{parent_b['candidate_id']}",
        "source_code": source_code.strip(),
        "fix_summary": fix_summary,
        "pending_rule_app_ids": rule_ids,
        "prompt_tokens_used": 0 # Tracked by LLM cache if needed
    }

def try_breeding_candidate(
    evolution_run_id: int,
    tool_name: str,
    hypothesis: dict | None,
    weakness_context: dict,
    db_path: str,
) -> dict | None:
    """
    Main hook called from evolution cycle before Day 25 generator strategies.
    """
    category = hypothesis.get("category") if hypothesis else None
    
    parents = select_parents(tool_name, category, db_path)
    if not parents:
        return None
        
    parent_a, parent_b = parents
    logger.info(f"Breeding {tool_name} from parents {parent_a['candidate_id']} ({parent_a['strategy']}) and {parent_b['candidate_id']} ({parent_b['strategy']})")
    
    candidate = generate_bred_candidate(tool_name, parent_a, parent_b, weakness_context, db_path)
    return candidate
