# -*- coding: utf-8 -*-

from aria.tools.base import BaseTool
from typing import Any

class StringProcessorTool(BaseTool):
    """
    Processes strings. Supports reversing strings.
    Input must contain an 'operation' (e.g., 'reverse') and 'text'.
    """

    def run(self, input_data: Any) -> Any:
        if not isinstance(input_data, dict):
            return {"success": False, "error": "Input must be a dictionary."}

        operation = input_data.get("operation")
        text = input_data.get("text")

        if text is None:
            return {"success": False, "error": "Missing 'text' parameter."}

        if not isinstance(text, str):
            return {"success": False, "error": "Parameter 'text' must be a string."}

        if operation == "reverse":
            # Use slicing to reverse the string efficiently
            return {"success": True, "output": text[::-1]}
        else:
            return {"success": False, "error": f"Unsupported operation: {operation}"}

    def test_cases(self) -> list[Any]:
        return [
            {"operation": "reverse", "text": "hello"},
            {"operation": "reverse", "text": ""},
            {"operation": "unknown", "text": "hello"},
            {"operation": "reverse", "text": "a" * 10000},
            {"operation": "reverse", "text": "a" * 100000},
        ]