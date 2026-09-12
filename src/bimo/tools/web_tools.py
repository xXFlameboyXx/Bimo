"""Web search and live internet information tools for Bimo.

Provides real-time internet search and page fetching capabilities using
DuckDuckGo and HTTP requests so Bimo can answer live questions (weather,
news, current events, facts, etc.) without requiring external search API keys.
"""

from __future__ import annotations

import logging
from typing import Any
import urllib.parse

from bimo.tools.base import BaseTool, ToolParameter, ToolResult
from bimo.tools.permissions import ToolPermission

logger = logging.getLogger(__name__)


class WebSearchTool(BaseTool):
    """Tool allowing Bimo to search the live internet via DuckDuckGo."""

    def __init__(self) -> None:
        super().__init__(
            name="web.search",
            description=(
                "Searches the live internet for real-time information, weather, news, "
                "facts, knowledge, or answers to questions."
            ),
            parameters=[
                ToolParameter(
                    name="query",
                    type="string",
                    description="The search query terms to look up on the internet.",
                    required=True,
                ),
                ToolParameter(
                    name="max_results",
                    type="integer",
                    description="Maximum number of search results to retrieve (1 to 5, default: 3).",
                    required=False,
                ),
            ],
            permission=ToolPermission.SAFE,
            timeout=12.0,
            strict_parameters=False,
        )

    def execute(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult(
                success=False,
                error="Parameter 'query' must be a non-empty string.",
                tool_name=self.name,
            )

        clean_query = query.strip()
        max_results = min(max(int(kwargs.get("max_results", 3)), 1), 5)

        try:
            import httpx
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }

            resp = httpx.post(
                "https://html.duckduckgo.com/html/",
                data={"q": clean_query},
                headers=headers,
                timeout=self.timeout,
            )

            if resp.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Search service returned status {resp.status_code}",
                    tool_name=self.name,
                )

            soup = BeautifulSoup(resp.text, "html.parser")
            results: list[dict[str, str]] = []

            for r in soup.select(".result__body"):
                title_tag = r.select_one(".result__title a")
                snippet_tag = r.select_one(".result__snippet")
                if title_tag and snippet_tag:
                    title = title_tag.get_text(strip=True)
                    snippet = snippet_tag.get_text(strip=True)
                    href = title_tag.get("href", "")
                    # Clean duckduckgo redirect link if present
                    if "uddg=" in href:
                        parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                        if "uddg" in parsed:
                            href = parsed["uddg"][0]
                    results.append({"title": title, "snippet": snippet, "url": href})
                    if len(results) >= max_results:
                        break

            if not results:
                return ToolResult(
                    success=True,
                    output=f"No internet search results found for: '{clean_query}'.",
                    tool_name=self.name,
                    metadata={"query": clean_query, "results_count": 0},
                )

            formatted_snippets = []
            for i, item in enumerate(results, 1):
                formatted_snippets.append(f"{i}. [{item['title']}]\n   {item['snippet']}")

            summary_text = "\n\n".join(formatted_snippets)
            return ToolResult(
                success=True,
                output=summary_text,
                tool_name=self.name,
                metadata={"query": clean_query, "results": results, "results_count": len(results)},
            )

        except Exception as exc:
            logger.warning("Web search failed for '%s': %s", clean_query, exc)
            return ToolResult(
                success=False,
                error=f"Web search error: {exc}",
                tool_name=self.name,
            )


class WebFetchTool(BaseTool):
    """Tool allowing Bimo to fetch and read readable text from a URL."""

    def __init__(self) -> None:
        super().__init__(
            name="web.fetch_page",
            description="Fetches and extracts readable text from a specific website URL.",
            parameters=[
                ToolParameter(
                    name="url",
                    type="string",
                    description="The HTTP or HTTPS webpage URL to read.",
                    required=True,
                ),
            ],
            permission=ToolPermission.SAFE,
            timeout=12.0,
            strict_parameters=False,
        )

    def execute(self, **kwargs: Any) -> ToolResult:
        url = kwargs.get("url")
        if not isinstance(url, str) or not url.strip().startswith(("http://", "https://")):
            return ToolResult(
                success=False,
                error="Parameter 'url' must be a valid http:// or https:// URL.",
                tool_name=self.name,
            )

        clean_url = url.strip()

        try:
            import httpx
            from bs4 import BeautifulSoup

            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }

            resp = httpx.get(
                clean_url,
                headers=headers,
                timeout=self.timeout,
                follow_redirects=True,
            )

            if resp.status_code != 200:
                return ToolResult(
                    success=False,
                    error=f"Webpage returned HTTP status {resp.status_code}",
                    tool_name=self.name,
                )

            soup = BeautifulSoup(resp.text, "html.parser")

            # Remove script and style elements
            for elem in soup(["script", "style", "nav", "footer", "header", "noscript"]):
                elem.extract()

            # Extract clean text
            title = soup.title.string.strip() if soup.title and soup.title.string else clean_url
            lines = (line.strip() for line in soup.get_text().splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = " ".join(chunk for chunk in chunks if chunk)

            # Limit text length to 1500 chars for concise spoken summary
            if len(text) > 1500:
                text = text[:1500] + "... [truncated]"

            output_summary = f"Title: {title}\nContent:\n{text}"
            return ToolResult(
                success=True,
                output=output_summary,
                tool_name=self.name,
                metadata={"url": clean_url, "title": title},
            )

        except Exception as exc:
            logger.warning("Failed to fetch webpage '%s': %s", clean_url, exc)
            return ToolResult(
                success=False,
                error=f"Failed to fetch webpage: {exc}",
                tool_name=self.name,
            )
