# -*- coding: utf-8 -*-

from aria.tools.base import BaseTool
from typing import Any
import httpx

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

    def validate_input(self, input_data: Any) -> bool:
        try:
            if not isinstance(input_data, dict):
                return False
            if "operation" not in input_data or "text" not in input_data:
                return False
            if not isinstance(input_data["operation"], str) or not isinstance(input_data["text"], str):
                return False
            return True
        except Exception as e:
            return False

    def validate_operation(self, operation: str) -> bool:
        return operation in ["reverse"]

    def validate_text(self, text: str) -> bool:
        return len(text) <= 100000

    def validate(self, input_data: Any) -> bool:
        return self.validate_input(input_data) and self.validate_operation(input_data["operation"]) and self.validate_text(input_data["text"])