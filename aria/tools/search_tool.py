"""
aria/tools/search_tool.py
──────────────────────────
Web search tool using DuckDuckGo Instant Answer API.
Falls back to HTML scraping with httpx + BeautifulSoup if the JSON API
returns no useful results.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import logging
from typing import Dict, List

import httpx
from bs4 import BeautifulSoup

from aria.tools.base import BaseTool, TestCase, ToolResult
from groq import Groq

class SearchTool(BaseTool):
    """
    Retrieves web search results for a given query.

    Input:
        query (str): The search query string.
        max_results (int, optional): Maximum results to return. Default: 3.

    Output:
        A list of dicts with 'title', 'url', and 'snippet' keys.
    """

    name = "search_tool"

    _DDGR_API = "https://api.duckduckgo.com/"
    _DDGR_HTML = "https://html.duckduckgo.com/html/"

    def __init__(self):
        self.logger = logging.getLogger(self.name)
        self.logger.setLevel(logging.INFO)
        self.groq = Groq(api_key="YOUR_API_KEY")

    def _get_json_response(self, query: str, params: Dict) -> httpx.Response:
        """Try the DuckDuckGo Instant Answer API (JSON)."""
        retries = 3
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=8.0) as client:
                    resp = client.get(self._DDGR_API, params=params)
                    resp.raise_for_status()
                    break
            except httpx.TimeoutException:
                if attempt < retries - 1:
                    self.logger.warning(f"Timeout on attempt {attempt + 1} of {retries}. Retrying...")
                else:
                    self.logger.error("All retries failed.")
                    raise
        else:
            self.logger.error("Failed to retrieve JSON response.")
            raise httpx.RequestError("Failed to retrieve JSON response.")

        return resp

    def _get_html_response(self, query: str, headers: Dict) -> httpx.Response:
        """Fallback: scrape DuckDuckGo HTML results."""
        retries = 3
        for attempt in range(retries):
            try:
                with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
                    resp = client.post(self._DDGR_HTML, data={"q": query})
                    resp.raise_for_status()
                    break
            except httpx.TimeoutException:
                if attempt < retries - 1:
                    self.logger.warning(f"Timeout on attempt {attempt + 1} of {retries}. Retrying...")
                else:
                    self.logger.error("All retries failed.")
                    raise
        else:
            self.logger.error("Failed to retrieve HTML response.")
            raise httpx.RequestError("Failed to retrieve HTML response.")

        return resp

    def _parse_json_response(self, resp: httpx.Response) -> List[Dict]:
        """Parse the JSON response from the DuckDuckGo Instant Answer API."""
        data = resp.json()
        results = []

        # Abstract (top answer)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", ""),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"][:300],
            })

        # Related topics
        for topic in data.get("RelatedTopics", []):
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", "")[:300],
                })

        return results

    def _parse_html_response(self, resp: httpx.Response) -> List[Dict]:
        """Parse the HTML response from the DuckDuckGo HTML results."""
        soup = BeautifulSoup(resp.text, "html.parser")
        results = []

        for result in soup.select(".result"):
            title_tag = result.select_one(".result__title a")
            snippet_tag = result.select_one(".result__snippet")
            if not title_tag:
                continue
            results.append({
                "title": title_tag.get_text(strip=True)[:120],
                "url": title_tag.get("href", ""),
                "snippet": snippet_tag.get_text(strip=True)[:300] if snippet_tag else "",
            })

        return results

    def run(self, input: Dict) -> ToolResult:
        query = input.get("query", "").strip()
        max_results = int(input.get("max_results", 3))

        if not query:
            return ToolResult(success=False, output=None, error="Empty query provided.")

        try:
            # Primary: DuckDuckGo Instant Answer JSON API
            params = {
                "q": query,
                "format": "json",
                "no_html": "1",
                "skip_disambig": "1",
            }
            resp = self._get_json_response(query, params)
            results = self._parse_json_response(resp)
            if results:
                return ToolResult(success=True, output=results)

            # Fallback: HTML scraping
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
                )
            }
            resp = self._get_html_response(query, headers)
            results = self._parse_html_response(resp)
            if results:
                return ToolResult(success=True, output=results)

            return ToolResult(
                success=False,
                output=None,
                error=f"No results found for query: '{query}'",
            )
        except httpx.RequestError as exc:
            self.logger.warning(f"Search request failed: {exc}")
            return ToolResult(success=False, output=None, error="Search request failed.")
        except Exception as exc:
            self.logger.error(f"Unexpected error: {exc}")
            return ToolResult(success=False, output=None, error=str(exc))

    def test_cases(self) -> List[TestCase]:
        return [
            TestCase(
                name="search_tool_001",
                input={"query": "python"},
                expected_output=[
                    {"title": "Python", "url": "", "snippet": "Python is a high-level, interpreted programming language."}
                ],
            ),
            TestCase(
                name="search_tool_002",
                input={"query": "test query"},
                expected_output=[
                    {"title": "Test Query", "url": "", "snippet": "This is a test query."}
                ],
            ),
            TestCase(
                name="search_tool_003",
                input={"query": "London"},
                expected_output=[
                    {"title": "London", "url": "", "snippet": "London is the capital of England."}
                ],
            ),
        ]