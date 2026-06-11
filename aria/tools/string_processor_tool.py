from typing import Any
from aria.tools.base import BaseTool

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
            # An intentionally slow/inefficient way to reverse a string
            # to give the improvement engine something to optimize!
            reversed_str = ""
            for char in text:
                reversed_str = char + reversed_str
            return {"success": True, "output": reversed_str}
        else:
            return {"success": False, "error": f"Unsupported operation: {operation}"}
