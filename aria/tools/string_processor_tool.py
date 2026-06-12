# -*- coding: utf-8 -*-

from aria.tools.base import BaseTool
from typing import Any, Dict
from groq import Groq
import httpx

class StringProcessorTool(BaseTool):
    name = "string_processor_tool"

    def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
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
        elif operation == "count_characters":
            # Use the groq library to count characters in the string
            groq = Groq("https://api.groq.io")
            try:
                response = groq.query(f"SELECT COUNT(*) FROM {text}")
                return {"success": True, "output": response["data"][0]["COUNT(*)"]}
            except Exception as e:
                return {"success": False, "error": str(e)}
        else:
            return {"success": False, "error": f"Unsupported operation: {operation}"}

    def test_cases(self) -> list[Dict[str, Any]]:
        return [
            {"operation": "reverse", "text": "hello"},
            {"operation": "reverse", "text": ""},
            {"operation": "unknown", "text": "hello"},
            {"operation": "reverse", "text": "a" * 10000},
            {"operation": "reverse", "text": "a" * 100000},
            {"operation": "count_characters", "text": "hello"},
            {"operation": "count_characters", "text": ""},
            {"operation": "count_characters", "text": "a" * 10000},
            {"operation": "count_characters", "text": "a" * 100000},
        ]