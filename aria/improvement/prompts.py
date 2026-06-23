"""
aria/improvement/prompts.py
────────────────────────────
Prompt templates used by the Improvement Engine when calling the Groq LLM.

The prompts are designed to:
  1. Give the LLM clear context about the BaseTool interface contract.
  2. Provide the weakness statistics and failure examples.
  3. Explicitly forbid dangerous patterns.
  4. Ask for ONLY the improved Python source code — no explanation, no markdown.
"""

from __future__ import annotations

import json

from aria.introspection.engine import WeaknessReport
from aria.introspection.self_model import self_model


SYSTEM_PROMPT = """\
You are an expert Python software engineer working on ARIA, an Autonomous Recursive Improvement Agent.

Your task is to improve a single Python tool module that is underperforming.

━━━ CONTRACT ━━━
The improved module MUST:
1. Contain exactly ONE class that inherits from BaseTool (imported from aria.tools.base).
2. Implement `run(self, input: dict) -> ToolResult` — same signature.
3. Implement `test_cases(self) -> list[TestCase]` — at least 3 test cases.
4. Keep the same `name` class attribute as the original.
5. The `__init__` method MUST NOT require any arguments (e.g., `def __init__(self):`). The framework will instantiate your class with zero arguments.
6. Be importable as a standalone Python 3.11 module.
7. MUST BE UNDER 300 LINES TOTAL. YOU MUST OBEY THIS CONSTRAINT.

━━━ FORBIDDEN ━━━
The improved module MUST NOT:
- Use `eval()`, `exec()`, or `__import__()`.
- Import: os, sys, subprocess, socket, shutil, pickle, ctypes, multiprocessing.
- Write to any file or database.
- Make network calls except via `httpx` or the `groq` python SDK.
- Use raw `httpx` or `requests` to call the Groq API. You MUST use the `groq` python SDK (`from groq import Groq`).
- When instantiating the Groq client, you MUST use keyword arguments (e.g. `Groq(api_key=...)`), never positional arguments.
- Access environment variables.
- Use threading or asyncio.
- Contain any syntax errors.
- Include hardcoded real-world data (URLs, Wikipedia snippets, API responses, etc.) as string literals inside `test_cases()`. Test case inputs must use short, simple placeholder strings only (e.g. `"python"`, `"test query"`, `"London"`). Long strings with special characters will cause syntax errors.
- Omit code using placeholders for logic (e.g. `# ... existing code ...`, `pass`, `...`). You MUST generate the FULL, complete, runnable Python code.

━━━ OUTPUT FORMAT ━━━
Return ONLY the complete, runnable Python source code of the improved module. It must not contain any placeholders or omitted logic.
Do NOT include markdown code fences (```python), explanations, or any text before or after the code.
The first line of your response must be a Python comment or import statement.
"""


from aria.config import settings

def build_improvement_prompt(report: WeaknessReport, strategy: str = "zero-shot") -> tuple[str, list[int]]:
    """
    Build the user-turn prompt for the improvement request.
    Returns (prompt_string, pending_rule_ids)
    """
    # Format recent failures as readable JSON
    failures_text = ""
    if report.recent_failures:
        fingerprints = {}
        for f in report.recent_failures:
            raw_msg = f.get('error_message', 'N/A') or 'N/A'
            raw_msg = str(raw_msg).strip()
            
            # Fingerprint: The first line of the error (usually the exception type/message)
            fingerprint = raw_msg.split('\n')[0][:150].strip()
            
            if fingerprint not in fingerprints:
                fingerprints[fingerprint] = {
                    "count": 0,
                    "full_msg": raw_msg,
                    "latencies": []
                }
            fingerprints[fingerprint]["count"] += 1
            fingerprints[fingerprint]["latencies"].append(f.get('latency_seconds', 0))
            
        # Format the top 5 unique fingerprints by occurrence
        for fp, data in sorted(fingerprints.items(), key=lambda x: x[1]["count"], reverse=True)[:5]:
            count = data["count"]
            avg_lat = sum(data["latencies"]) / max(1, len(data["latencies"]))
            
            # Keep a sensible truncation on the representative message to protect the payload limit
            msg_str = data["full_msg"]
            trunc_msg = msg_str if len(msg_str) <= 400 else msg_str[:400] + "..."
            
            failures_text += f"  - [{count} occurrences] Error: {trunc_msg} | Avg Latency: {avg_lat:.2f}s\n"
    else:
        failures_text = "  (no failure records available)\n"

    # Measured Token Overhead (Phase 1 Memory Sections):
    # - similar_failures_text: ~150-200 tokens max (up to 5 items, ~150 chars each)
    # - history_text (fixes): ~200-250 tokens max (up to 10 items, ~80 chars each)
    # Total phase 1 memory budget: ~450 tokens.
    
    similar_failures_text = ""
    if getattr(report, "similar_failures", None):
        similar_failures_text = "SIMILAR PAST FAILURES (most recent first):\n"
        for i, f in enumerate(report.similar_failures, 1):
            err_msg = f.get("error_message", "")
            if len(err_msg) > 120:
                err_msg = err_msg[:117] + "..."
            
            ts = f.get("timestamp", "")
            ts_str = str(ts)[:10] if ts else "Unknown"
            source = f.get("source", "Unknown")
            err_type = f.get("error_type", "Error")
            sig = f.get("traceback_signature", "unknown")
            
            similar_failures_text += f"{i}. [{ts_str}, {source}] {err_type} — signature {sig}\n   \"{err_msg}\"\n"
        similar_failures_text += "\n"

    reasons_text = "\n".join(f"  - {r}" for r in report.reasons)
    
    history_text = ""
    if getattr(report, "successful_fixes", None):
        history_text += "PREVIOUSLY SUCCESSFUL FIXES FOR THIS PATTERN:\n"
        for i, fix in enumerate(report.successful_fixes, 1):
            fit_delta = fix.get("fitness_delta")
            if fit_delta is None:
                fit_delta = 0.0
            summary = (fix.get("fix_summary") or "").replace("\n", " ").strip()
            history_text += f"{i}. [fitness +{fit_delta:.2f}] \"{summary}\"\n"
        history_text += "\n"

    if getattr(report, "failed_fixes", None):
        history_text += "APPROACHES ALREADY TRIED AND REJECTED/ROLLED BACK — DO NOT REPEAT:\n"
        for i, fix in enumerate(report.failed_fixes, 1):
            res = fix.get("result", "rejected")
            summary = (fix.get("fix_summary") or "").replace("\n", " ").strip()
            reason = (fix.get("rejection_reason") or "").replace("\n", " ").strip()
            suffix = f" — {reason}" if reason else ""
            history_text += f"{i}. [{res}] \"{summary}\"{suffix}\n"
        history_text += "\n"

    # Incorporate Self-Model patterns
    model_data = self_model.get_model()
    improvement_engine_data = model_data.get("components", {}).get("improvement_engine", {})
    
    # TRUNCATION FIX: Limit unbounded self-model growth to the 5 most recent
    patterns = improvement_engine_data.get("recent_failure_patterns", [])[-5:]
    system_patterns = model_data.get("system_wide_patterns", [])[-5:]
    
    self_model_text = ""
    if patterns or system_patterns:
        self_model_text = "━━━ ARIA SELF-MODEL KNOWLEDGE ━━━\n"
        self_model_text += "You must avoid these known failure patterns from your previous improvement cycles:\n"
        for p in patterns:
            self_model_text += f"  - [Improvement Engine Failure] {p}\n"
        for p in system_patterns:
            self_model_text += f"  - [System-wide Pattern] {p}\n"
        self_model_text += "\n"
        
    directive_text = ""
    category = None
    if getattr(report, "hypothesis", None):
        hyp = report.hypothesis
        category = hyp.get("category")
        directive_text = (
            "## DIRECTIVE (from Root Cause Analysis)\n"
            f"Root cause: {hyp.get('root_cause_summary')}\n"
            f"Proposed fix: {hyp.get('proposed_fix_summary')}\n"
            f"Target this tool specifically: {report.tool_name}\n\n"
        )
        
    from aria.knowledge.applications import select_rules_for_prompt
    rules = select_rules_for_prompt(category, str(settings.db_path))
    
    rules_text = ""
    rule_ids = []
    if rules:
        rules_text = "## Engineering Principles (apply where relevant)\n"
        for i, rule in enumerate(rules, 1):
            rules_text += f"{i}. [confidence {rule['confidence']:.2f}, {rule['category']}] {rule['rule_text']}\n"
            rule_ids.append(rule['id'])
        rules_text += "\n"

    strategy_text = ""
    if strategy == "mutation":
        strategy_text = "\nSTRATEGY: Mutation\nInstead of a full rewrite, apply a small, targeted mutation to the logic or error handling to fix the weakness.\n"
    elif strategy == "breeding":
        strategy_text = "\nSTRATEGY: Breeding\nCombine the best aspects of the current code with defensive patterns and robust error handling.\n"
    elif strategy == "structural":
        strategy_text = "\nSTRATEGY: Structural\nRefactor the overall structure of the tool into smaller, more modular helper functions within the class.\n"
    elif strategy == "zero-shot":
        strategy_text = "\nSTRATEGY: Zero-Shot\nPerform a complete, robust rewrite of the tool from scratch to eliminate the weaknesses.\n"

    prompt = f"""{self_model_text}IMPROVEMENT REQUEST
═══════════════════
Tool Name:        {report.tool_name}
Severity:         {report.severity.upper()}
Fitness Score:    {report.fitness_score:.2f}
Success Rate:     {report.success_rate:.1%}
p90 Latency:      {report.p90_latency:.2f}s
Total Executions: {report.total_executions}
Failures:         {report.failure_count}

DETECTED WEAKNESSES:
{reasons_text}

{similar_failures_text}{history_text}RECENT FAILURE SAMPLES:
{failures_text}
{strategy_text}
CURRENT SOURCE CODE (improve this):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{report.source_code}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTRUCTIONS:
{directive_text}{rules_text}- Analyze the weaknesses above.
- Identify the root cause of failures, high latency, or high resource usage.
- Write an improved version of this tool that addresses those weaknesses to maximize the Fitness Score.
- Make the code more robust, efficient, and cost-effective.
- Do NOT change the tool's name or its input/output contract.
- Return ONLY the improved Python source code. No markdown, no explanation.
"""
    return prompt, rule_ids
