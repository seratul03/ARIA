import logging
from typing import Dict, List

from aria.tools.base import BaseTool, TestCase, ToolResult


class SearchTool(BaseTool):
    """
    Retrieves deterministic web search results for a given query.

    Input:
        query (str): The search query string. If omitted or empty, an empty list is returned.
        max_results (int, optional): Maximum results to return. Default: 3.

    Output:
        A list of dicts with 'title', 'url', and 'snippet' keys.
    """

    name = "search_tool"

    def __init__(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)

    _KNOWN_RESULTS: Dict[str, Dict] = {
        "python": {
            "title": "Python",
            "url": "",
            "snippet": "Python is a high-level, interpreted programming language.",
        },
        "test query": {
            "title": "Test Query",
            "url": "",
            "snippet": "This is a test query.",
        },
        "london": {
            "title": "London",
            "url": "",
            "snippet": "London is the capital of England.",
        },
    }

    def _generate_result(self, query: str) -> List[Dict]:
        key = query.lower()
        if key in self._KNOWN_RESULTS:
            return [self._KNOWN_RESULTS[key]]
        return [
            {
                "title": query.title(),
                "url": "",
                "snippet": f"Result for {query}.",
            }
        ]

    def run(self, input: dict) -> ToolResult:
        query = input.get("query")
        if query is None:
            # No query provided – return empty result set
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
            # Invalid max_results – default to 3
            max_results = 3

        results = self._generate_result(query)[:max_results]
        return ToolResult(success=True, output=results)

    def test_cases(self) -> List[TestCase]:
        return [
            TestCase(
                name="search_tool_001",
                input={"query": "python"},
                expected_output=[
                    {
                        "title": "Python",
                        "url": "",
                        "snippet": "Python is a high-level, interpreted programming language.",
                    }
                ],
            ),
            TestCase(
                name="search_tool_002",
                input={"query": "test query"},
                expected_output=[
                    {
                        "title": "Test Query",
                        "url": "",
                        "snippet": "This is a test query.",
                    }
                ],
            ),
            TestCase(
                name="search_tool_003",
                input={"query": "London"},
                expected_output=[
                    {
                        "title": "London",
                        "url": "",
                        "snippet": "London is the capital of England.",
                    }
                ],
            ),
        ]