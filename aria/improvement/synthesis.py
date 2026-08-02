"""
aria/improvement/synthesis.py
─────────────────────────────
The Tool Synthesis Engine handles building brand new tools from scratch
based on a text specification. It generates code, sends it to the Docker
Sandbox, and uses error feedback to iteratively fix issues until it passes.
"""

from __future__ import annotations

import re
import time
import textwrap
from dataclasses import dataclass
import logging

from aria.config import settings
from aria.core.rate_limiter import groq_limiter
from aria.improvement.engine import _clean_code, _looks_like_python
from aria.gatekeeper.sandbox import DockerSandbox

logger = logging.getLogger(__name__)

SYNTHESIS_SYSTEM_PROMPT = """You are an expert Python engineer inside ARIA, an autonomous recursive improvement agent.
Your task is to build a completely new tool from scratch based on the user's specification.

CRITICAL RULES:
1. You must write a Python class that inherits from `aria.tools.base.BaseTool`.
2. The class must have a `name` property, a `description` property, a synchronous `def run(self, input_data: dict) -> dict` method (NOT async), and a `test_cases(self) -> list` method.
3. The `run` method MUST return a dictionary with at least two keys: `"success": True` (or False on error) and `"output": <your data>`.
4. The `test_cases` method must return a list of dictionaries. Each dictionary must have:
   - "name": A string description of the test.
   - "input": A dictionary of inputs to pass to the tool.
   - "expected_success": A boolean (True/False) indicating if this input should succeed.
4. Your code will be executed in a Docker sandbox. The sandbox has NO INTERNET ACCESS. 
   - You MUST use the `httpx` library (e.g. `httpx.get()`) to make actual HTTP requests in your `run` method.
   - You MUST use `respx` to mock these requests for testing. To do this, you MUST define a method `def mock_apis(self, respx_mock):` in your class.
   - **CRITICAL**: DO NOT use the `@respx.mock` decorator! The Sandbox will pass the `respx_mock` object directly to your method. Using the decorator will crash the sandbox with a "multiple values for argument" error.
   - Example mock: `def mock_apis(self, respx_mock): import httpx; respx_mock.get("https://api.com").mock(return_value=httpx.Response(200, json={"data": 1}))`
   - **CRITICAL**: The Sandbox will automatically call `mock_apis` before testing. NEVER call `mock_apis` yourself inside `run`.
   - **CRITICAL**: Because `mock_apis` only provides one static mock response, your `test_cases()` MUST ONLY contain ONE test case that tests the successful response (`expected_success: True`). DO NOT write test cases for HTTP errors or failures, as they will fail the Sandbox validation!
   - **CRITICAL**: DO NOT include `expected_output` in your test cases. Only `name`, `input`, and `expected_success` are supported.
5. Do NOT use `os`, `sys` (except sys.exit), `subprocess`, `shutil` or other system-level libraries. It will be rejected by the Gatekeeper.
6. Output ONLY valid Python code inside a ```python ``` block. No conversational text.
"""

def build_synthesis_prompt(tool_name: str, specification: str, error_log: str | None = None, previous_code: str | None = None) -> str:
    prompt = f"Tool Name: {tool_name}\n"
    prompt += f"Specification:\n{specification}\n\n"
    
    if error_log and previous_code:
        prompt += "PREVIOUS ATTEMPT FAILED in the sandbox. Here is the code you wrote:\n"
        prompt += "```python\n" + previous_code + "\n```\n\n"
        prompt += "And here is the error traceback from the sandbox:\n"
        prompt += "```\n" + error_log + "\n```\n\n"
        prompt += "Fix the errors and output the corrected complete Python class."
        
    return prompt


@dataclass
class SynthesisResult:
    tool_name: str
    success: bool
    generated_code: str | None = None
    error: str | None = None
    attempts: int = 0


class ToolSynthesisEngine:
    def __init__(self) -> None:
        self.sandbox = DockerSandbox()

    def synthesize(self, tool_name: str, specification: str, max_retries: int = 3) -> SynthesisResult:
        """
        Attempt to generate a new tool and validate it.
        Will retry up to `max_retries` times if the sandbox fails.
        """
        try:
            from groq import Groq
        except ImportError:
            return SynthesisResult(tool_name, success=False, error="Groq package not installed.")

        # Use the synthesis API key if provided, else fallback to default
        api_key = settings.synthesis_groq_api_key or settings.groq_api_key
        client = Groq(api_key=api_key)

        previous_code = None
        error_log = None

        for attempt in range(1, max_retries + 1):
            logger.info(f"[Synthesis] Attempt {attempt}/{max_retries} for '{tool_name}'...")
            groq_limiter.acquire()
            
            user_prompt = build_synthesis_prompt(tool_name, specification, error_log, previous_code)
            
            try:
                response = client.chat.completions.create(
                    model=settings.groq_model,
                    messages=[
                        {"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    max_tokens=settings.llm_max_tokens,
                    temperature=0.3,
                )
            except Exception as exc:
                return SynthesisResult(tool_name, success=False, error=f"LLM Generation failed: {exc}", attempts=attempt)

            raw_code = response.choices[0].message.content or ""
            cleaned_code = _clean_code(raw_code)

            if not _looks_like_python(cleaned_code):
                print(f"RAW CODE:\n{raw_code}\n\nCLEANED CODE:\n{cleaned_code}")
                error_log = "Error: Output did not look like valid Python code containing a class and def run."
                previous_code = cleaned_code
                continue

            # Run in Sandbox
            sandbox_result = self.sandbox.run(
                tool_name=tool_name,
                candidate_source=cleaned_code,
                current_stats=None,  # No current stats for a brand new tool
                raw_results_only=False
            )

            # Check if referee approved it (meaning it passed tests and static checks)
            # For synthesis, since we don't have a baseline, the referee logic might fail if it strictly compares to a baseline.
            # However, our Sandbox handles new tools if we bypass the relative referee comparison.
            # We'll use raw_results_only to test it directly without the Baseline comparison.
            
            raw_results = self.sandbox.run(
                tool_name=tool_name,
                candidate_source=cleaned_code,
                raw_results_only=True,
                session_tests=[]  # Relying only on the tool's embedded test_cases()
            )
            
            if isinstance(raw_results, dict) and raw_results.get("approved") is False:
                # This usually means static validation failed (e.g. malicious import)
                error_log = raw_results.get("rejection_reason", "Unknown Sandbox failure")
                previous_code = cleaned_code
                continue

            if isinstance(raw_results, list):
                # We got a list of test results
                if not raw_results:
                    error_log = "Error: Sandbox returned 0 test results. Make sure test_cases() returns a valid list."
                    previous_code = cleaned_code
                    continue
                
                failed_tests = [r for r in raw_results if not r.get("passed", False)]
                if failed_tests:
                    error_log = "Error: Some tests failed.\n"
                    for ft in failed_tests:
                        error_log += f"- Test '{ft.get('name')}' failed. Error: {ft.get('error')}\n"
                    print(f"FAILED CODE:\n{cleaned_code}")
                    previous_code = cleaned_code
                    continue
                else:
                    # ALL tests passed!
                    logger.info(f"[Synthesis] Success on attempt {attempt} for '{tool_name}'!")
                    return SynthesisResult(
                        tool_name=tool_name,
                        success=True,
                        generated_code=cleaned_code,
                        attempts=attempt
                    )
            else:
                error_log = f"Error: Unexpected sandbox output: {raw_results}"
                previous_code = cleaned_code
                continue
                
        return SynthesisResult(
            tool_name=tool_name, 
            success=False, 
            error=f"Failed after {max_retries} attempts. Last error: {error_log}",
            generated_code=previous_code,
            attempts=max_retries
        )
