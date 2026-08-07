from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Callable
from urllib.parse import urlparse

from personal_news_agent.services.article_fetch import canonicalize_url
from personal_news_agent.core.models import NormalizedArticle, SectionConfig, SourceConfig
from personal_news_agent.services.search_index import ArticleSearchIndex
from personal_news_agent.services.source_adapter import ListPageAdapter
from personal_news_agent.services.source_registry import SourceRegistryService
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.url_store import CrawlUrlStore


def _error_message(exc: Exception) -> str:
    return str(exc).strip() or type(exc).__name__


class CrawlScheduler:
    def __init__(
        self,
        registry: SourceRegistryService,
        store: NewsStore,
        url_store: CrawlUrlStore | None = None,
        search_index: ArticleSearchIndex | None = None,
        adapter_factory: Callable[[SourceConfig], ListPageAdapter] | None = None,
    ):
        self.registry = registry
        self.store = store
        self.url_store = url_store or CrawlUrlStore()
        self.search_index = search_index or ArticleSearchIndex()
        self.adapter_factory = adapter_factory or ListPageAdapter
        self._round_lock = asyncio.Lock()
        self._source_locks: dict[str, asyncio.Lock] = {}
        self._source_last_finished: dict[str, float] = {}

    async def crawl_category(self, category: str, per_section_limit: int = 10, fetch_articles: int = 1) -> dict:
        results: list[dict] = []
        for source in self.registry.get_sources_by_category(category):
            if not source.crawl_enabled:
                continue
            adapter = self.adapter_factory(source)
            for section in source.sections:
                if section.category != category or not section.crawl_enabled:
                    continue
                results.append(
                    await self._crawl_section_rate_limited(
                        adapter=adapter,
                        source=source,
                        section=section,
                        operation="crawl_section",
                        per_section_limit=per_section_limit,
                        fetch_articles=fetch_articles,
                    )
                )
        return {"category": category, "results": results}

    def due_plan(self, category: str | None = None, limit: int = 50) -> dict:
        sections = self._due_sections(category=category, limit=limit)
        due = [section for section in sections if section["due"]]
        return {
            "category": category,
            "sections": sections,
            "due_count": len(due),
            "total_returned": len(sections),
            "mysql_ready": self.url_store.ready,
        }

    async def crawl_due(
        self,
        category: str | None = None,
        limit: int = 20,
        per_section_limit: int = 10,
        fetch_articles: int = 1,
        workers: int = 2,
    ) -> dict:
        if self._round_lock.locked():
            return {"status": "skipped", "reason": "crawl_round_in_progress", "planned_sections": 0, "results": [], "saved_articles": 0, "errors": 0}
        async with self._round_lock:
            return await self._crawl_due_round(category, limit, per_section_limit, fetch_articles, workers)

    async def _crawl_due_round(self, category: str | None, limit: int, per_section_limit: int, fetch_articles: int, workers: int) -> dict:
        due_sections = _interleave_by_source([section for section in self._due_sections(category=category, limit=limit) if section["due"]])
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        for due in due_sections:
            queue.put_nowait(due)
        worker_count = min(max(1, int(workers)), 4, max(1, len(due_sections)))
        results: list[dict] = []

        async def run_worker(worker_id: int) -> None:
            while True:
                due = await queue.get()
                if due is None:
                    queue.task_done()
                    return
                try:
                    try:
                        result = await self._crawl_due_section(due, per_section_limit, fetch_articles)
                    except Exception as exc:
                        result = {
                            "source_id": due.get("source_id"),
                            "section_key": due.get("section_key"),
                            "error": _error_message(exc),
                        }
                    result["worker_id"] = worker_id
                    results.append(result)
                finally:
                    queue.task_done()

        tasks = [asyncio.create_task(run_worker(index + 1)) for index in range(worker_count)]
        for _ in tasks:
            queue.put_nowait(None)
        await queue.join()
        await asyncio.gather(*tasks)
        results.sort(key=lambda item: (item.get("source_id", ""), item.get("section_key", "")))
        return {
            "status": "completed",
            "category": category,
            "workers": worker_count,
            "planned_sections": len(due_sections),
            "results": results,
            "saved_articles": sum(item.get("saved", 0) for item in results),
            "duplicate_articles": sum(item.get("duplicates", 0) for item in results),
            "skipped_articles": sum(item.get("skipped", 0) for item in results),
            "fetch_errors": sum(item.get("fetch_errors", 0) for item in results),
            "errors": sum(1 for item in results if "error" in item),
            "mysql_ready": self.url_store.ready,
        }

    async def _crawl_due_section(self, due: dict, per_section_limit: int, fetch_articles: int) -> dict:
        source = self.registry.get_source(due["source_id"])
        section = next((item for item in source.sections if item.key == due["section_key"]), None)
        if not section:
            return {"source_id": due["source_id"], "section_key": due["section_key"], "error": "section_not_found"}
        return await self._crawl_section_rate_limited(
            adapter=self.adapter_factory(source),
            source=source,
            section=section,
            operation="crawl_due_section",
            per_section_limit=per_section_limit,
            fetch_articles=fetch_articles,
            include_category=True,
            include_error_tags=True,
        )

    def _due_sections(self, category: str | None, limit: int) -> list[dict]:
        if self.url_store.ready:
            valid_sections = {
                (source.source_id, section.key)
                for source in self.registry.all_sources()
                if source.crawl_enabled
                for section in source.sections
                if section.crawl_enabled and (not category or section.category == category)
            }
            # MySQL is a durable URL history and can contain rows for sources
            # that have since been disabled or removed from sources.yaml. Read
            # a little beyond the requested limit so those stale rows cannot
            # crowd current sections out of a crawl round.
            query_limit = min(500, max(limit * 2, limit + 50))
            try:
                mysql_due = self.url_store.list_due(category=category, limit=query_limit, url_type="section")
            except Exception as exc:
                self.store.log("crawl_url_store", "error", "list_due", {"error": _error_message(exc)})
                return self.store.due_sections(category=category, limit=limit)
            return [
                _mysql_due_to_section(item)
                for item in mysql_due
                if (item.get("source_id"), item.get("section_key")) in valid_sections
            ][:limit]
        return self.store.due_sections(category=category, limit=limit)

    async def _save_article(self, article: NormalizedArticle, interval_minutes: int) -> dict:
        saved = self.store.save_article(article)
        article_id = saved["article_id"]
        self._url_store_call(
            "mark_article_ok",
            article.url,
            lambda: self.url_store.mark_fetch_ok(
                article.url,
                article_id=article_id,
                content_hash=article.content_hash,
                interval_minutes=interval_minutes,
            ),
        )
        if saved["duplicate"]:
            return saved
        try:
            await self.search_index.index_article(article.__dict__)
        except Exception as exc:
            self.store.log("index_article", "error", article.id, {"error": str(exc), "url": article.url})
        return saved

    def _url_store_call(self, operation: str, target: str, callback: Callable[[], None]) -> bool:
        try:
            callback()
            return True
        except Exception as exc:
            self.store.log("crawl_url_store", "error", target, {"operation": operation, "error": _error_message(exc)})
            return False

    async def _crawl_section_rate_limited(
        self,
        adapter: ListPageAdapter,
        source: SourceConfig,
        section: SectionConfig,
        **kwargs,
    ) -> dict:
        lock = self._source_locks.setdefault(source.source_id, asyncio.Lock())
        async with lock:
            loop = asyncio.get_running_loop()
            last_finished = self._source_last_finished.get(source.source_id)
            if last_finished is not None:
                remaining = source.rate_limit.min_interval_seconds - (loop.time() - last_finished)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            try:
                return await self._crawl_section(adapter=adapter, source=source, section=section, **kwargs)
            finally:
                self._source_last_finished[source.source_id] = loop.time()

    async def _crawl_section(
        self,
        adapter: ListPageAdapter,
        source: SourceConfig,
        section: SectionConfig,
        operation: str,
        per_section_limit: int,
        fetch_articles: int,
        include_category: bool = False,
        include_error_tags: bool = False,
    ) -> dict:
        base = {"source_id": source.source_id, "section_key": section.key}
        if include_category:
            base["category"] = section.category
        try:
            discovered_links = await adapter.crawl_section(section.key, per_section_limit)
            links = []
            seen_urls: set[str] = set()
            for link in discovered_links:
                canonical_url = canonicalize_url(link.url)
                if canonical_url in seen_urls:
                    continue
                seen_urls.add(canonical_url)
                links.append(replace(link, url=canonical_url))
            self._url_store_call(
                "upsert_links",
                section.url,
                lambda: self.url_store.upsert_links(source, section.key, section.category, links),
            )
            saved = 0
            duplicates = 0
            skipped = 0
            attempted = 0
            fetch_errors = 0
            last_fetch_error = None
            attempt_budget = min(len(links), max(fetch_articles, fetch_articles * 3))
            for link in links:
                if saved + duplicates >= fetch_articles or attempted >= attempt_budget:
                    break
                if self.store.find_article_by_url(link.url):
                    skipped += 1
                    continue
                attempted += 1
                try:
                    raw = await adapter.fetch_article(link.url)
                    if _is_url_title(raw.title, raw.url):
                        raw = replace(raw, title=link.title)
                    if link.published_at and raw.published_at is None:
                        raw = replace(raw, published_at=link.published_at)
                    normalized = adapter.normalize_article(raw, section.key, section.category)
                    result = await self._save_article(normalized, source.crawl_interval_minutes)
                    if result["duplicate"]:
                        duplicates += 1
                    else:
                        saved += 1
                except Exception as exc:
                    fetch_errors += 1
                    last_fetch_error = _error_message(exc)
                    self._url_store_call(
                        "mark_article_error",
                        link.url,
                        lambda: self.url_store.mark_fetch_error(link.url, last_fetch_error, source.crawl_interval_minutes),
                    )
                    self.store.log("fetch_article", "error", link.url, {"error": last_fetch_error})
            self.store.mark_section_crawled(source.source_id, section.key)
            self._url_store_call(
                "mark_section_ok",
                section.url,
                lambda: self.url_store.mark_fetch_ok(section.url, interval_minutes=source.crawl_interval_minutes),
            )
            result = base | {
                "links": len(links),
                "attempted": attempted,
                "saved": saved,
                "duplicates": duplicates,
                "skipped": skipped,
                "fetch_errors": fetch_errors,
                "tags": list(source.tags),
            }
            if last_fetch_error:
                result["last_fetch_error"] = last_fetch_error
            self.store.log(operation, "ok", f"{source.source_id}:{section.key}", result)
            return result
        except Exception as exc:
            error = _error_message(exc)
            self._url_store_call(
                "mark_section_error",
                section.url,
                lambda: self.url_store.mark_fetch_error(section.url, error, source.crawl_interval_minutes),
            )
            result = base | {"error": error}
            if include_error_tags:
                result["tags"] = list(source.tags)
            self.store.log(operation, "error", f"{source.source_id}:{section.key}", {"error": error})
            return result


def _is_url_title(title: str, article_url: str) -> bool:
    value = str(title or "").strip()
    parsed = urlparse(value)
    return not value or value == article_url or (parsed.scheme in {"http", "https"} and bool(parsed.netloc))


def _interleave_by_source(items: list[dict]) -> list[dict]:
    """Round-robin portals so two workers do not cluster requests by source."""
    buckets: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in items:
        source_id = str(item.get("source_id") or "")
        if source_id not in buckets:
            buckets[source_id] = []
            order.append(source_id)
        buckets[source_id].append(item)
    result: list[dict] = []
    while any(buckets.values()):
        for source_id in order:
            if buckets[source_id]:
                result.append(buckets[source_id].pop(0))
    return result


def _mysql_due_to_section(row: dict) -> dict:
    return {
        "id": row["id"],
        "source_id": row["source_id"],
        "section_key": row["section_key"],
        "name": row.get("title") or row["section_key"],
        "category": row["category"],
        "url": row["url"],
        "crawl_strategy": (row.get("metadata") or {}).get("crawl_strategy"),
        "crawl_enabled": True,
        "last_crawled_at": row.get("last_fetched_at"),
        "source_name": row["source_id"],
        "source_tags": row.get("tags") or [],
        "priority": row.get("priority") or 5,
        "crawl_interval_minutes": row.get("fetch_interval_minutes") or 15,
        "due": True,
    }
