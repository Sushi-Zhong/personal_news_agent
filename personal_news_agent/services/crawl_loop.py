from __future__ import annotations

import asyncio
import json
from typing import Any

from personal_news_agent.services.crawl import CrawlScheduler
from personal_news_agent.services.store import NewsStore


class ContinuousCrawlService:
    """Run non-overlapping due-crawl rounds with a small fixed worker pool."""

    def __init__(
        self,
        scheduler: CrawlScheduler,
        store: NewsStore,
        *,
        workers: int = 2,
        due_limit: int = 20,
        per_section_limit: int = 20,
        fetch_articles: int = 5,
        idle_seconds: float = 30.0,
        topic_extractor: Any | None = None,
        extraction_limit: int = 20,
    ):
        self.scheduler = scheduler
        self.store = store
        self.workers = min(max(1, workers), 4)
        self.due_limit = max(1, due_limit)
        self.per_section_limit = max(1, per_section_limit)
        self.fetch_articles = max(0, fetch_articles)
        self.idle_seconds = max(1.0, idle_seconds)
        self.topic_extractor = topic_extractor
        self.extraction_limit = max(1, extraction_limit)

    async def run_once(self, category: str | None = None) -> dict[str, Any]:
        result = await self.scheduler.crawl_due(
            category=category,
            limit=self.due_limit,
            per_section_limit=self.per_section_limit,
            fetch_articles=self.fetch_articles,
            workers=self.workers,
        )
        extraction = None
        if self.topic_extractor:
            extraction = await self.topic_extractor.process_pending(limit=self.extraction_limit)
            result["topic_extraction"] = extraction
        self.store.log(
            "continuous_crawl_round",
            result.get("status", "unknown"),
            category or "all",
            {
                "planned_sections": result.get("planned_sections", 0),
                "saved_articles": result.get("saved_articles", 0),
                "duplicate_articles": result.get("duplicate_articles", 0),
                "fetch_errors": result.get("fetch_errors", 0),
                "errors": result.get("errors", 0),
                "workers": result.get("workers", self.workers),
                "topics_processed": (extraction or {}).get("processed", 0),
            },
        )
        return result

    async def run_forever(self, stop_event: asyncio.Event | None = None, category: str | None = None) -> None:
        stop = stop_event or asyncio.Event()
        while not stop.is_set():
            result = await self.run_once(category=category)
            extraction = result.get("topic_extraction") or {}
            print(
                json.dumps(
                    {
                        "event": "crawl_round",
                        "status": result.get("status", "unknown"),
                        "category": category,
                        "planned_sections": result.get("planned_sections", 0),
                        "saved_articles": result.get("saved_articles", 0),
                        "duplicate_articles": result.get("duplicate_articles", 0),
                        "fetch_errors": result.get("fetch_errors", 0),
                        "errors": result.get("errors", 0),
                        "topics_processed": extraction.get("processed", 0),
                        "topic_errors": len(extraction.get("errors") or []),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if result.get("planned_sections", 0) > 0:
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.idle_seconds)
            except TimeoutError:
                pass
