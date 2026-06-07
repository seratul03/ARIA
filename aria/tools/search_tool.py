"""
aria/tools/search_tool.py
──────────────────────────
Web search tool using DuckDuckGo Instant Answer API.
Falls back to HTML scraping with httpx + BeautifulSoup if the JSON API
returns no useful results.

This tool is intentionally improvable by ARIA's Improvement Engine.
"""

from __future__ import annotations

import time

import httpx
from bs4 import BeautifulSoup

from aria.tools.base import BaseTool, TestCase, ToolResult


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

    def run(self, input: dict) -> ToolResult:
        query = input.get("query", "").strip()
        max_results = int(input.get("max_results", 3))

        if not query:
            return ToolResult(success=False, output=None, error="Empty query provided.")

        try:
            # Primary: DuckDuckGo Instant Answer JSON API
            results = self._json_search(query, max_results)
            if results:
                return ToolResult(success=True, output=results)

            # Fallback: HTML scraping
            results = self._html_search(query, max_results)
            if results:
                return ToolResult(success=True, output=results)

            return ToolResult(
                success=False,
                output=None,
                error=f"No results found for query: '{query}'",
            )
        except httpx.TimeoutException:
            return ToolResult(success=False, output=None, error="Search request timed out.")
        except Exception as exc:
            return ToolResult(success=False, output=None, error=str(exc))

    def _json_search(self, query: str, max_results: int) -> list[dict]:
        """Try the DuckDuckGo Instant Answer API (JSON)."""
        params = {
            "q": query,
            "format": "json",
            "no_html": "1",
            "skip_disambig": "1",
        }
        with httpx.Client(timeout=8.0) as client:
            resp = client.get(self._DDGR_API, params=params)
            resp.raise_for_status()
            data = resp.json()

        results = []

        # Abstract (top answer)
        if data.get("AbstractText"):
            results.append({
                "title": data.get("Heading", query),
                "url": data.get("AbstractURL", ""),
                "snippet": data["AbstractText"][:300],
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:80],
                    "url": topic.get("FirstURL", ""),
                    "snippet": topic.get("Text", "")[:300],
                })
            if len(results) >= max_results:
                break

        return results[:max_results]

    def _html_search(self, query: str, max_results: int) -> list[dict]:
        """Fallback: scrape DuckDuckGo HTML results."""
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            )
        }
        with httpx.Client(timeout=10.0, headers=headers, follow_redirects=True) as client:
            resp = client.post(self._DDGR_HTML, data={"q": query})
            resp.raise_for_status()

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
            if len(results) >= max_results:
                break

        return results

    def test_cases(self) -> list[TestCase]:
        return [
            TestCase(
                name="basic_query",
                input={"query": "Python programming language"},
                expected_success=True,
                description="Standard query should return results.",
            ),
            TestCase(
                name="empty_query",
                input={"query": ""},
                expected_success=False,
                description="Empty query must fail gracefully.",
            ),
            TestCase(
                name="specific_topic",
                input={"query": "Albert Einstein", "max_results": 2},
                expected_success=True,
                description="Famous person query should return results.",
            ),
            TestCase(
                name="single_word",
                input={"query": "Quantum"},
                expected_success=True,
                description="Single word query should still work.",
            ),
        ]
