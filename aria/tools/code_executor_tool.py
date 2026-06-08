"""
aria/tools/code_executor_tool.py
─────────────────────────────────
Takes a coding topic, asks Groq to generate code, then asks Groq to improve it.
Saves the before and after code to code_result.txt and returns the output.
"""

from __future__ import annotations

import os
from aria.tools.base import BaseTool, TestCase, ToolResult
from aria.config import settings

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
            from groq import Groq
            client = Groq(api_key=settings.groq_api_key)
            
            # 1. Initial Code
            res1 = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": f"Write python code for: {topic}. Return ONLY code without markdown formatting blocks."}],
                temperature=0.2
            )
            before_code = res1.choices[0].message.content.strip()
            
            # 2. Improved Code
            res2 = client.chat.completions.create(
                model=settings.groq_model,
                messages=[{"role": "user", "content": f"Here is some python code for '{topic}':\n\n{before_code}\n\nImprove this code for performance, readability, and safety. Return ONLY the improved python code without markdown formatting blocks."}],
                temperature=0.2
            )
            after_code = res2.choices[0].message.content.strip()
            
            out_str = (
                f"Generated and improved code for: {topic}\n\n"
                f"--- Before Code ---\n{before_code}\n\n"
                f"--- After Code ---\n{after_code}"
            )
            
            return ToolResult(success=True, output=out_str)
            
        except Exception as exc:
            return ToolResult(success=False, output=None, error=f"LLM Code Generation Error: {exc}")
