"""
aria/tools/code_executor_tool.py
─────────────────────────────────
Takes a coding topic, asks Groq to generate code, then asks Groq to improve it.
Saves the before and after code to code_result.txt and returns the output.
"""

from __future__ import annotations

from aria.tools.base import BaseTool, TestCase, ToolResult
from aria.config import settings
import httpx


class CodeExecutorTool(BaseTool):
    """
    Generates and improves code based on a topic using Groq.
    """
    name = "code_executor_tool"

    def run(self, input: dict) -> ToolResult:
        topic = str(input.get("topic", "")).strip()
        if not topic:
            return ToolResult(success=False, output=None, error="No coding topic provided.")

        try:
            prompt = (
                f"Write python code for: {topic}. "
                f"Then improve it for performance, readability, and safety. "
                f"Return your answer in EXACTLY this format (no markdown blocks):\n\n"
                f"--- Before Code ---\n<original code here>\n\n"
                f"--- After Code ---\n<improved code here>"
            )
            msg = [{"role": "user", "content": prompt}]
            llm_output = self._generate_completion_with_fallback(msg)

            out_str = f"Generated and improved code for: {topic}\n\n{llm_output}"

            return ToolResult(success=True, output=out_str)

        except httpx.HTTPError as exc:
            return ToolResult(success=False, output=None, error=f"LLM Code Generation Error: {exc}")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"LLM Code Generation Error: {exc}")

    def _call_llm(self, api_key: str, endpoint: str, model: str, messages: list[dict], max_tokens: int, extra_headers: dict = None) -> str:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if extra_headers:
            headers.update(extra_headers)
        
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens
        }
        resp = httpx.post(endpoint, headers=headers, json=payload, timeout=30.0)
        resp.raise_for_status()
        return resp.json().get("choices", [{}])[0].get("message", {}).get("content", "").strip()

    def _generate_completion_with_fallback(self, messages: list[dict], max_tokens: int = 256) -> str:
        # 1. Groq Key 1
        try:
            return self._call_llm(
                api_key=settings.groq_api_key,
                endpoint="https://api.groq.com/openai/v1/chat/completions",
                model=settings.groq_model,
                messages=messages,
                max_tokens=max_tokens
            )
        except httpx.HTTPStatusError as e:
            if e.response.status_code != 429:
                raise

        # 2. Synthesis Groq Key
        if getattr(settings, "synthesis_groq_api_key", None) and settings.synthesis_groq_api_key != settings.groq_api_key:
            try:
                return self._call_llm(
                    api_key=settings.synthesis_groq_api_key,
                    endpoint="https://api.groq.com/openai/v1/chat/completions",
                    model=settings.groq_model,
                    messages=messages,
                    max_tokens=max_tokens
                )
            except httpx.HTTPStatusError as e:
                if e.response.status_code != 429:
                    raise
                
        # 3. OpenRouter Fallback
        openrouter_model = getattr(settings, "openrouter_model", "openrouter/auto")
        return self._call_llm(
            api_key=settings.openrouter_api_key,
            endpoint="https://openrouter.ai/api/v1/chat/completions",
            model=openrouter_model,
            messages=messages,
            max_tokens=max_tokens,
            extra_headers={"HTTP-Referer": "https://aria.ai", "X-Title": "ARIA Code Executor"}
        )

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="code_executor_tool_001",
                input={"topic": "Write a function to calculate the sum of all numbers in a list"},
                expected_output=(
                    "Generated and improved code for: Write a function to calculate the sum of all numbers in a list\n\n"
                    "--- Before Code ---\n"
                    "def sum_list(numbers):\n"
                    "    sum = 0\n"
                    "    for num in numbers:\n"
                    "        sum += num\n"
                    "    return sum\n\n"
                    "--- After Code ---\n"
                    "def sum_list(numbers):\n"
                    "    return sum(numbers)\n"
                )
            ),
            TestCase(
                name="code_executor_tool_002",
                input={"topic": "Create a class to represent a bank account"},
                expected_output=(
                    "Generated and improved code for: Create a class to represent a bank account\n\n"
                    "--- Before Code ---\n"
                    "class BankAccount:\n"
                    "    def __init__(self, account_number, balance):\n"
                    "        self.account_number = account_number\n"
                    "        self.balance = balance\n\n"
                    "--- After Code ---\n"
                    "class BankAccount:\n"
                    "    def __init__(self, account_number, balance=0):\n"
                    "        self.account_number = account_number\n"
                    "        self.balance = balance\n"
                )
            ),
            TestCase(
                name="code_executor_tool_003",
                input={"topic": "Write a function to check if a string is a palindrome"},
                expected_output=(
                    "Generated and improved code for: Write a function to check if a string is a palindrome\n\n"
                    "--- Before Code ---\n"
                    "def is_palindrome(s):\n"
                    "    reversed_s = s[::-1]\n"
                    "    return s == reversed_s\n\n"
                    "--- After Code ---\n"
                    "def is_palindrome(s):\n"
                    "    return s == s[::-1]\n"
                )
            )
        ]