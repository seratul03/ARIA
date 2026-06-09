"""
aria/improvement/engine.py
───────────────────────────
The Improvement Engine takes a WeaknessReport and uses the Groq LLM to
generate an improved version of the flagged tool's source code.

Flow:
  1. Acquire rate limiter permit
  2. Build prompt from WeaknessReport
  3. Call Groq LLM
  4. Extract and clean the generated code
  5. Return generated code string to the caller (Agent Core)
     → The caller passes it to the Gatekeeper for validation

The Improvement Engine does NOT write any files or deploy anything.
That responsibility belongs to the Agent Core + Gatekeeper pipeline.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.introspection.engine import WeaknessReport
from aria.improvement.prompts import SYSTEM_PROMPT, build_improvement_prompt


@dataclass
class ImprovementResult:
    """
    The output of one improvement attempt.
    """
    tool_name: str
    generated_code: str | None      # None if LLM call failed
    success: bool
    error: str | None = None
    tokens_used: int = 0
    elapsed_seconds: float = 0.0


import textwrap

def _clean_code(raw: str) -> str:
    """
    Strip any accidentally included markdown fences and conversational text
    from the LLM response, and dedent to fix unexpected indent syntax errors.
    """
    # Extract ONLY the content inside ```python ... ``` blocks if present
    match = re.search(r"```(?:python)?\s*(.*?)```", raw, flags=re.DOTALL | re.IGNORECASE)
    if match:
        code = match.group(1).strip()
    else:
        # Fallback: try to strip conversational text at the beginning
        lines = raw.split('\n')
        start_idx = 0
        for i, line in enumerate(lines):
            # Find the first line that looks like Python code
            if re.match(r"^\s*(import |from |class |def |@|#|r?\"\"\")", line):
                start_idx = i
                break
        code = '\n'.join(lines[start_idx:]).strip()
        
    return textwrap.dedent(code).strip()


def _looks_like_python(code: str) -> bool:
    """Basic sanity check that the response looks like Python code."""
    if len(code) < 50:
        return False
    # Should have at least one class definition and one def
    has_class = bool(re.search(r"^class\s+\w+", code, re.MULTILINE))
    has_def = bool(re.search(r"^\s+def\s+run\b", code, re.MULTILINE))
    return has_class and has_def


class ImprovementEngine:
    """
    Calls Groq LLM to generate improved tool source code.
    """

    def generate_improvement(self, report: WeaknessReport) -> ImprovementResult:
        """
        Generate an improved version of the tool described in `report`.

        Returns an ImprovementResult. On failure, result.success is False
        and result.error explains what went wrong.
        """
        start = time.monotonic()

        # Acquire rate limiter — this may block briefly
        groq_limiter.acquire()

        try:
            from groq import Groq  # imported here to keep startup fast
        except ImportError:
            return ImprovementResult(
                tool_name=report.tool_name,
                generated_code=None,
                success=False,
                error="Groq package not installed. Run: pip install groq",
            )

        user_prompt = build_improvement_prompt(report)

        try:
            client = Groq(api_key=settings.groq_api_key)
            response = client.chat.completions.create(
                model=settings.groq_model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=3000,
                temperature=0.2,    # Low temperature for more deterministic code
                stop=None,
            )

            elapsed = time.monotonic() - start
            raw_code = response.choices[0].message.content or ""
            tokens_used = (
                response.usage.total_tokens if response.usage else 0
            )

            cleaned = _clean_code(raw_code)

            if not _looks_like_python(cleaned):
                return ImprovementResult(
                    tool_name=report.tool_name,
                    generated_code=None,
                    success=False,
                    error=(
                        "LLM response does not look like valid Python tool code. "
                        f"Response length: {len(cleaned)} chars."
                    ),
                    tokens_used=tokens_used,
                    elapsed_seconds=elapsed,
                )

            return ImprovementResult(
                tool_name=report.tool_name,
                generated_code=cleaned,
                success=True,
                tokens_used=tokens_used,
                elapsed_seconds=elapsed,
            )

        except Exception as exc:
            elapsed = time.monotonic() - start
            error_msg = str(exc)

            # Detect rate limit errors explicitly
            if "429" in error_msg or "rate_limit" in error_msg.lower():
                error_msg = (
                    f"Groq rate limit hit. ARIA will retry after the cooldown period. "
                    f"Original error: {error_msg}"
                )

            return ImprovementResult(
                tool_name=report.tool_name,
                generated_code=None,
                success=False,
                error=error_msg,
                elapsed_seconds=elapsed,
            )
