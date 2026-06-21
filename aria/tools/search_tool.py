import logging
import urllib.parse
from typing import Dict, List, Any

import httpx
from bs4 import BeautifulSoup

from aria.tools.base import BaseTool, TestCase, ToolResult

class SearchTool(BaseTool):
    """
    Retrieves web search results for a given query using DuckDuckGo HTML.
    """

    name = "search_tool"

    def __init__(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)
        self.client = httpx.Client(timeout=10.0, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    def run(self, input: Dict) -> ToolResult:
        query = input.get("query")
        if not query or not isinstance(query, str) or not query.strip():
            return ToolResult(success=True, output=[])
        query = query.strip()

        max_results = input.get("max_results", 3)
        try:
            max_results = int(max_results)
            if max_results < 1:
                max_results = 3
        except Exception:
            max_results = 3

        try:
            encoded_query = urllib.parse.quote(query)
            url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
            
            response = self.client.get(url)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            
            for a in soup.find_all("a", class_="result__url", href=True):
                if len(results) >= max_results:
                    break
                parent = a.find_parent("div", class_="result")
                if not parent:
                    continue
                title_elem = parent.find("h2", class_="result__title")
                snippet_elem = parent.find("a", class_="result__snippet")
                
                title = title_elem.text.strip() if title_elem else ""
                snippet = snippet_elem.text.strip() if snippet_elem else ""
                url = a["href"].strip()
                
                if url.startswith("//duckduckgo.com/l/?uddg="):
                    try:
                        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
                        if "uddg" in parsed:
                            url = parsed["uddg"][0]
                    except:
                        pass

                if title or snippet:
                    results.append({"title": title, "url": url, "snippet": snippet})
            
            return ToolResult(success=True, output=results)
        except Exception as e:
            self.logger.error(f"Error searching for query '{query}': {e}")
            return ToolResult(success=False, error=str(e), output=[])

    def mock_apis(self, respx_mock: Any) -> None:
        import re
        def mock_duckduckgo(request):
            query = ""
            try:
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(str(request.url)).query)
                if "q" in parsed:
                    query = parsed["q"][0].lower()
            except:
                pass
                
            html = f'''
            <div class="result">
                <h2 class="result__title"><a href="#">{query.title()}</a></h2>
                <a class="result__snippet">This is a mocked result snippet for {query}.</a>
                <a class="result__url" href="https://example.com/{query}">https://example.com</a>
            </div>
            '''
            return httpx.Response(200, text=html)

        respx_mock.get(re.compile(r"https://html\.duckduckgo\.com/html/.*")).mock(side_effect=mock_duckduckgo)

    def test_cases(self) -> List[TestCase]:
        return [
            TestCase(
                name="search_tool_001",
                input={"query": "python"},
                expected_success=True,
                output_contains="Python"
            ),
            TestCase(
                name="search_tool_002",
                input={"query": "test query"},
                expected_success=True,
                output_contains="Test Query"
            ),
            TestCase(
                name="search_tool_003",
                input={"query": "London"},
                expected_success=True,
                output_contains="London"
            ),
            TestCase(
                name="search_tool_004",
                input={"query": "Quantum"},
                expected_success=True,
                output_contains="Quantum"
            ),
            TestCase(
                name="search_tool_005",
                input={"query": "error query"},
                expected_success=True,
                output_contains="Error Query"
            ),
        ]