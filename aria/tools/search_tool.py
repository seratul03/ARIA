import logging
from typing import Dict, List

from aria.tools.base import BaseTool, TestCase, ToolResult
from groq import Groq

class SearchTool(BaseTool):
    """
    Retrieves deterministic web search results for a given query.
    """

    name = "search_tool"

    def __init__(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)
        self.groq = Groq(api_key="YOUR_API_KEY")

    def run(self, input: Dict) -> ToolResult:
        query = input.get("query")
        if query is None:
            return ToolResult(success=True, output=[])
        if not isinstance(query, str):
            query = str(query)
        query = query.strip()
        if not query:
            return ToolResult(success=True, output=[])

        max_results = input.get("max_results", 3)
        try:
            max_results = int(max_results)
            if max_results < 1:
                raise ValueError
        except Exception:
            max_results = 3

        try:
            results = self.groq.search(query, max_results=max_results)
            return ToolResult(success=True, output=results)
        except Exception as e:
            self.logger.error(f"Error searching for query '{query}': {e}")
            return ToolResult(success=False, output=[])

    def test_cases(self) -> List[TestCase]:
        return [
            TestCase(
                name="search_tool_001",
                input={"query": "python"},
                expected_output=[
                    {"title": "Python", "url": "", "snippet": "Python is a high-level, interpreted programming language."},
                ],
            ),
            TestCase(
                name="search_tool_002",
                input={"query": "test query"},
                expected_output=[
                    {"title": "Test Query", "url": "", "snippet": "This is a test query."},
                ],
            ),
            TestCase(
                name="search_tool_003",
                input={"query": "London"},
                expected_output=[
                    {"title": "London", "url": "", "snippet": "London is the capital of England."},
                ],
            ),
            TestCase(
                name="search_tool_004",
                input={"query": "Quantum"},
                expected_output=[
                    {"title": "Quantum", "url": "", "snippet": "Quantum is a branch of physics."},
                ],
            ),
            TestCase(
                name="search_tool_005",
                input={"query": "error query"},
                expected_output=[
                    {"title": "Error Query", "url": "", "snippet": "This is an error query."},
                ],
            ),
        ]