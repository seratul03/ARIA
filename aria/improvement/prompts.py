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


SYSTEM_PROMPT = """\
You are an expert Python software engineer working on ARIA, an Autonomous Recursive Improvement Agent.

Your task is to improve a single Python tool module that is underperforming.

━━━ CONTRACT ━━━
The improved module MUST:
1. Contain exactly ONE class that inherits from BaseTool (imported from aria.tools.base).
2. Implement `run(self, input: dict) -> ToolResult` — same signature.
3. Implement `test_cases(self) -> list[TestCase]` — at least 3 test cases.
4. Keep the same `name` class attribute as the original.
5. Be importable as a standalone Python 3.11 module.
6. Be under 300 lines total.

━━━ FORBIDDEN ━━━
The improved module MUST NOT:
- Use `eval()`, `exec()`, or `__import__()`.
- Import: os, sys, subprocess, socket, shutil, pickle, ctypes, multiprocessing.
- Write to any file or database.
- Make network calls outside of httpx (httpx is allowed if the tool needs it).
- Access environment variables.
- Use threading or asyncio.
- Contain any syntax errors.

━━━ OUTPUT FORMAT ━━━
Return ONLY the complete Python source code of the improved module.
Do NOT include markdown code fences (```python), explanations, or any text before or after the code.
The first line of your response must be a Python comment or import statement.
"""


def build_improvement_prompt(report: WeaknessReport) -> str:
    """
    Build the user-turn prompt for the improvement request.
    """
    # Format recent failures as readable JSON
    failures_text = ""
    if report.recent_failures:
        failures_text = "\n".join(
            f"  - Error: {f.get('error_message', 'N/A')} | "
            f"Latency: {f.get('latency_seconds', 0):.2f}s"
            for f in report.recent_failures[:5]
        )
    else:
        failures_text = "  (no failure records available)"

    reasons_text = "\n".join(f"  - {r}" for r in report.reasons)
    
    rejected_history_text = ""
    if report.recent_improvement_failures:
        rejected_history_text = "\nPAST IMPROVEMENT ATTEMPTS THAT FAILED:\n" + "\n".join(
            f"  - Rejected Reason: {f.get('reason', 'N/A')}"
            for f in report.recent_improvement_failures
        ) + "\nWARNING: Do NOT repeat these mistakes!\n"

    prompt = f"""IMPROVEMENT REQUEST
═══════════════════
Tool Name:        {report.tool_name}
Severity:         {report.severity.upper()}
Success Rate:     {report.success_rate:.1%} (threshold: must be ≥ 70%)
p90 Latency:      {report.p90_latency:.2f}s
Total Executions: {report.total_executions}
Failures:         {report.failure_count}

DETECTED WEAKNESSES:
{reasons_text}

RECENT FAILURE SAMPLES:
{failures_text}
{rejected_history_text}
CURRENT SOURCE CODE (improve this):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{report.source_code}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

INSTRUCTIONS:
- Analyze the weaknesses above.
- Identify the root cause of failures or high latency.
- Write an improved version of this tool that addresses those weaknesses.
- Make the code more robust: add better error handling, retries, or alternative strategies.
- Do NOT change the tool's name or its input/output contract.
- Return ONLY the improved Python source code. No markdown, no explanation.
"""
    return prompt
