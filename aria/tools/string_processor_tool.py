# -*- coding: utf-8 -*-

from aria.tools.base import BaseTool, TestCase, ToolResult
from typing import Any, Dict

class StringProcessorTool(BaseTool):
    name = "string_processor_tool"

    def run(self, input_data: Dict[str, Any]) -> ToolResult:
        if not isinstance(input_data, dict):
            return ToolResult(success=False, output=None, error="Input must be a dictionary.")

        operation = input_data.get("operation")
        text = input_data.get("text")

        if text is None:
            return ToolResult(success=False, output=None, error="Missing 'text' parameter.")

        if not isinstance(text, str):
            return ToolResult(success=False, output=None, error="Parameter 'text' must be a string.")

        if operation == "reverse":
            return ToolResult(success=True, output=text[::-1], error=None)
        elif operation == "count_characters":
            return ToolResult(success=True, output=len(text), error=None)
        else:
            return ToolResult(success=False, output=None, error=f"Unsupported operation: {operation}")

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(name="reverse_basic", input={"operation": "reverse", "text": "hello"}, expected_success=True),
            TestCase(name="reverse_empty", input={"operation": "reverse", "text": ""}, expected_success=True),
            TestCase(name="unknown_op", input={"operation": "unknown", "text": "hello"}, expected_success=False),
            TestCase(name="count_basic", input={"operation": "count_characters", "text": "hello"}, expected_success=True),
            TestCase(name="count_empty", input={"operation": "count_characters", "text": ""}, expected_success=True),
            TestCase(name="missing_text", input={"operation": "reverse"}, expected_success=False)
        ]