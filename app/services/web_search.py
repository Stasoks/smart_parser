from __future__ import annotations

import asyncio
from typing import Iterable

import httpx

from app.config import Settings
from app.schemas import WebEvidence


class WebSearchService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, max_results: int | None = None) -> list[WebEvidence]:
        limit = max_results or self.settings.web_search_max_results
        if self.settings.web_search_provider == "disabled":
            return []
        if self.settings.web_search_provider == "tavily" and self.settings.tavily_api_key:
            return await self._tavily(query, limit)
        return await self._ddgs(query, limit)

    async def multi_search(self, queries: Iterable[str], max_results_each: int = 4) -> list[WebEvidence]:
        unique = list(dict.fromkeys(q.strip() for q in queries if q and q.strip()))[:4]
        chunks = await asyncio.gather(
            *(self.search(q, max_results_each) for q in unique),
            return_exceptions=True,
        )
        output: list[WebEvidence] = []
        seen: set[str] = set()
        for chunk in chunks:
            if isinstance(chunk, Exception):
                continue
            for item in chunk:
                if item.url not in seen:
                    seen.add(item.url)
                    output.append(item)
        return output

    async def _tavily(self, query: str, limit: int) -> list[WebEvidence]:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": self.settings.tavily_api_key,
                    "query": query,
                    "max_results": limit,
                    "search_depth": "basic",
                },
            )
            response.raise_for_status()
            data = response.json()
        return [
            WebEvidence(
                query=query,
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
            )
            for item in data.get("results", [])[:limit]
            if item.get("url")
        ]

    async def _ddgs(self, query: str, limit: int) -> list[WebEvidence]:
        def run() -> list[WebEvidence]:
            try:
                from ddgs import DDGS
            except ImportError:
                return []
            items = DDGS().text(query, region="ru-ru", safesearch="moderate", max_results=limit)
            return [
                WebEvidence(
                    query=query,
                    title=item.get("title", ""),
                    url=item.get("href") or item.get("url") or "",
                    snippet=item.get("body", ""),
                )
                for item in items
                if item.get("href") or item.get("url")
            ]

        return await asyncio.to_thread(run)
