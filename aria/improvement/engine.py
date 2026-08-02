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

import logging
import re
import time
from dataclasses import dataclass, field

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.introspection.engine import WeaknessReport
from aria.improvement.prompts import SYSTEM_PROMPT, build_improvement_prompt

logger = logging.getLogger(__name__)


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
    pending_rule_app_ids: list[int] = field(default_factory=list)


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
                
        # Find the end of the code by finding the first line that looks like trailing chat
        end_idx = len(lines)
        for i, line in enumerate(lines[start_idx:], start_idx):
            line_stripped = line.strip().lower()
            if line_stripped and re.match(r"^(hope this helps|let me know|here is the|this implementation|note:)", line_stripped):
                end_idx = i
                break
                
        code = '\n'.join(lines[start_idx:end_idx]).strip()
        
    return textwrap.dedent(code).strip()


def _looks_like_python(code: str) -> bool:
    """Basic sanity check that the response looks like Python code."""
    if len(code) < 50:
        return False
    # Should have at least one class definition and one def
    has_class = bool(re.search(r"^class\s+\w+", code, re.MULTILINE))
    has_def = bool(re.search(r"^\s+(?:async\s+)?def\s+run\b", code, re.MULTILINE))
    return has_class and has_def


class ImprovementEngine:
    """
    Calls Groq LLM to generate improved tool source code.
    """

    def generate_improvement(self, report: WeaknessReport, cycle_id: str | None = None, strategy: str = "zero-shot") -> ImprovementResult:
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

        user_prompt, rule_ids = build_improvement_prompt(report, strategy)
        
        from aria.knowledge.applications import log_rule_applications
        pending_rule_app_ids = log_rule_applications(rule_ids, cycle_id, str(settings.db_path))
        
        try:
            from aria.core.tracer import emit_trace
            emit_trace("improvement", "prompt_constructed", {"tool": report.tool_name, "prompt_length": len(user_prompt)})
        except ImportError:
            pass
        try:
            import httpx
            
            def _call_llm(api_key: str, endpoint: str, model: str, messages: list[dict], extra_headers: dict = None) -> str:
                headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                if extra_headers:
                    headers.update(extra_headers)

                full_content = ""
                current_messages = list(messages)
                continuation = 0

                while True:
                    resp = httpx.post(endpoint, headers=headers, json={
                        "model": model,
                        "messages": current_messages,
                        "temperature": 0.2,
                        "max_tokens": settings.llm_max_tokens,
                    }, timeout=30.0)
                    resp.raise_for_status()

                    data = resp.json()
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("message", {}).get("content", "")
                    finish_reason = choice.get("finish_reason", "stop")

                    full_content += content

                    if finish_reason == "length":
                        continuation += 1
                        warning_msg = (
                            f"⚠️  [ARIA] LLM response was TRUNCATED (continuation #{continuation}). "
                            f"Requesting LLM to finish generation..."
                        )
                        print(warning_msg, flush=True)
                        logger.warning(warning_msg)

                        # Ask the LLM to pick up exactly where it left off
                        current_messages = current_messages + [
                            {"role": "assistant", "content": full_content},
                            {"role": "user", "content": "Continue exactly where you left off. Do not repeat any code you already wrote."},
                        ]
                    else:
                        # finish_reason == "stop" — generation is complete
                        break

                return full_content.strip()

            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]

            raw_code = None
            last_error = None
            
            # 1. Groq Key 1
            try:
                raw_code = _call_llm(settings.groq_api_key, "https://api.groq.com/openai/v1/chat/completions", settings.groq_model, messages)
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code != 429: raise
            
            # 2. Synthesis Groq Key
            if not raw_code and getattr(settings, "synthesis_groq_api_key", None):
                try:
                    raw_code = _call_llm(settings.synthesis_groq_api_key, "https://api.groq.com/openai/v1/chat/completions", settings.groq_model, messages)
                except httpx.HTTPStatusError as e:
                    last_error = e
                    if e.response.status_code != 429: raise
            
            # 3. OpenRouter Fallback
            if not raw_code:
                openrouter_model = getattr(settings, "openrouter_model", "openrouter/auto")
                try:
                    raw_code = _call_llm(settings.openrouter_api_key, "https://openrouter.ai/api/v1/chat/completions", openrouter_model, messages, {"HTTP-Referer": "https://aria.ai", "X-Title": "ARIA Engine"})
                except Exception as e:
                    last_error = e

            if not raw_code:
                raise Exception(f"All LLM fallbacks failed. Last error: {last_error}")

            elapsed = time.monotonic() - start
            tokens_used = 0  # We can't easily extract tokens without modifying the HTTPX function to return it, so we default to 0.

            try:
                from aria.core.tracer import emit_trace
                emit_trace("improvement", "llm_call_details", {"success": True, "tokens_used": tokens_used, "elapsed_seconds": elapsed})
            except ImportError:
                pass

            cleaned = _clean_code(raw_code)

            if not _looks_like_python(cleaned):
                try:
                    from aria.core.tracer import emit_trace
                    emit_trace("improvement", "candidate_evaluation", {"tool": report.tool_name, "valid_python": False, "length": len(cleaned)})
                except ImportError:
                    pass
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
                    pending_rule_app_ids=pending_rule_app_ids,
                )

            try:
                from aria.core.tracer import emit_trace
                emit_trace("improvement", "candidate_evaluation", {"tool": report.tool_name, "valid_python": True, "length": len(cleaned)})
            except ImportError:
                pass
                
            return ImprovementResult(
                tool_name=report.tool_name,
                generated_code=cleaned,
                success=True,
                tokens_used=tokens_used,
                elapsed_seconds=elapsed,
                pending_rule_app_ids=pending_rule_app_ids,
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

            try:
                from aria.core.tracer import emit_trace
                emit_trace("improvement", "llm_call_details", {"success": False, "error": error_msg, "elapsed_seconds": elapsed})
            except ImportError:
                pass

            return ImprovementResult(
                tool_name=report.tool_name,
                generated_code=None,
                success=False,
                error=error_msg,
                elapsed_seconds=elapsed,
            )
