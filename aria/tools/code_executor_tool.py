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
from groq import Groq

class CodeExecutorTool(BaseTool):
    """
    Generates and improves code based on a topic using Groq.
    """
    name = "code_executor_tool"

    def _call_with_continuation(self, client: Groq, messages: list[dict], label: str) -> str:
        """
        Calls the Groq LLM and automatically continues if the response is
        truncated (finish_reason == 'length'). Prints a ⚠️ warning to the
        terminal each time a continuation is needed.
        """
        full_content = ""
        current_messages = list(messages)
        continuation = 0

        while True:
            resp = client.chat.completions.create(
                model=settings.groq_model,
                messages=current_messages,
                temperature=0.2,
                max_tokens=settings.llm_max_tokens,
            )
            choice = resp.choices[0]
            content = choice.message.content or ""
            finish_reason = choice.finish_reason

            full_content += content

            if finish_reason == "length":
                continuation += 1
                warning_msg = (
                    f"⚠️  [ARIA] {label} response was TRUNCATED "
                    f"(continuation #{continuation}). "
                    f"Requesting LLM to finish generation..."
                )
                print(warning_msg, flush=True)

                # Feed what we have back and ask LLM to continue
                current_messages = current_messages + [
                    {"role": "assistant", "content": full_content},
                    {"role": "user", "content": "Continue exactly where you left off. Do not repeat any code you already wrote."},
                ]
            else:
                # finish_reason == "stop" — generation complete
                break

        return full_content.strip()

    def run(self, input: dict) -> ToolResult:
        topic = str(input.get("topic", "")).strip()
        if not topic:
            return ToolResult(success=False, output=None, error="No coding topic provided.")

        try:
            client = Groq(api_key=settings.groq_api_key)

            # 1. Initial Code
            before_code = self._call_with_continuation(
                client,
                messages=[{"role": "user", "content": f"Write python code for: {topic}. Return ONLY code without markdown formatting blocks."}],
                label="Initial Code Generation",
            )

            # 2. Improved Code
            after_code = self._call_with_continuation(
                client,
                messages=[{"role": "user", "content": f"Here is some python code for '{topic}':\n\n{before_code}\n\nImprove this code for performance, readability, and safety. Return ONLY the improved python code without markdown formatting blocks."}],
                label="Code Improvement",
            )

            out_str = (
                f"Generated and improved code for: {topic}\n\n"
                f"--- Before Code ---\n{before_code}\n\n"
                f"--- After Code ---\n{after_code}"
            )

            return ToolResult(success=True, output=out_str)

        except httpx.HTTPError as exc:
            return ToolResult(success=False, output=None, error=f"LLM Code Generation Error: {exc}")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"LLM Code Generation Error: {exc}")

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