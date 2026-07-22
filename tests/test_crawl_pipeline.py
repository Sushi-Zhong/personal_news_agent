from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from personal_news_agent.core.models import RawArticle, RawArticleLink
from personal_news_agent.services.article_fetch import _looks_like_article_url, canonicalize_url
from personal_news_agent.services.crawl import CrawlScheduler
from personal_news_agent.services.crawl_loop import ContinuousCrawlService
from personal_news_agent.services.search_index import ArticleSearchIndex
from personal_news_agent.services.source_registry import SourceRegistryService
from personal_news_agent.services.store import NewsStore


class FakeAdapter:
    active = 0
    max_active = 0

    def __init__(self, source):
        self.source = source

    async def crawl_section(self, section_key: str, limit: int):
        FakeAdapter.active += 1
        FakeAdapter.max_active = max(FakeAdapter.max_active, FakeAdapter.active)
        await asyncio.sleep(0.01)
        FakeAdapter.active -= 1
        return [
            RawArticleLink(
                source_id=self.source.source_id,
                section_key=section_key,
                title=f"{self.source.name} 测试文章标题",
                url=f"https://{self.source.root_domain}/2026/{section_key}.html?utm_source=index#top",
            )
        ][:limit]

    async def fetch_article(self, url: str):
        return RawArticle(
            source_id=self.source.source_id,
            url=canonicalize_url(url),
            title=f"{self.source.name} 测试文章标题",
            summary="测试摘要",
            content=f"{self.source.source_id} 的正文内容，长度足够用于测试文章抓取和去重入库。",
        )

    def normalize_article(self, raw, section_key: str, category: str):
        from personal_news_agent.services.source_adapter import ListPageAdapter

        return ListPageAdapter(self.source).normalize_article(raw, section_key, category)


def _registry(path: Path) -> SourceRegistryService:
    path.write_text(
        """
sources:
  - source_id: source_a
    name: Source A
    root_domain: a.example.com
    source_type: portal
    categories: [sports]
    sections:
      - {key: sports, name: Sports A, category: sports, url: https://a.example.com/sports/}
  - source_id: source_b
    name: Source B
    root_domain: b.example.com
    source_type: portal
    categories: [military]
    sections:
      - {key: military, name: Military B, category: military, url: https://b.example.com/military/}
""",
        encoding="utf-8",
    )
    registry = SourceRegistryService(path)
    registry.load()
    return registry


def test_canonicalize_url_removes_tracking_and_fragment():
    assert canonicalize_url("HTTPS://News.Example.com:443/a.html?b=2&utm_source=x&a=1#part") == "https://news.example.com/a.html?a=1&b=2"


def test_article_link_filter_rejects_feedback_pages():
    assert _looks_like_article_url("http://news.sina.com.cn/feedback/post.html") is False


def test_continuous_crawl_writes_one_round_summary_and_stops(capsys):
    stop = asyncio.Event()

    class StopAfterOneScheduler:
        async def crawl_due(self, **kwargs):
            stop.set()
            return {
                "status": "completed",
                "planned_sections": 1,
                "saved_articles": 2,
                "duplicate_articles": 1,
                "errors": 0,
                "workers": kwargs["workers"],
            }

    class LogStore:
        def log(self, *args, **kwargs):
            return None

    service = ContinuousCrawlService(StopAfterOneScheduler(), LogStore(), workers=2)
    asyncio.run(service.run_forever(stop_event=stop))

    import json

    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "event": "crawl_round",
        "status": "completed",
        "category": None,
        "planned_sections": 1,
        "saved_articles": 2,
        "duplicate_articles": 1,
        "errors": 0,
        "topics_processed": 0,
        "topic_errors": 0,
    }


def test_due_crawl_uses_two_workers_and_skips_existing_urls(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    FakeAdapter.active = 0
    FakeAdapter.max_active = 0
    scheduler = CrawlScheduler(registry, store, search_index=ArticleSearchIndex(), adapter_factory=FakeAdapter)

    first = asyncio.run(scheduler.crawl_due(limit=10, per_section_limit=5, fetch_articles=1, workers=2))

    assert first["status"] == "completed"
    assert first["workers"] == 2
    assert first["saved_articles"] == 2
    assert FakeAdapter.max_active == 2

    with store.connect() as conn:
        conn.execute("UPDATE news_sections SET last_crawled_at = NULL")
    second = asyncio.run(scheduler.crawl_due(limit=10, per_section_limit=5, fetch_articles=1, workers=2))
    assert second["saved_articles"] == 0
    assert second["skipped_articles"] == 2


def test_store_deduplicates_same_content_across_urls(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    source = registry.get_source("source_a")
    adapter = FakeAdapter(source)
    raw = asyncio.run(adapter.fetch_article("https://a.example.com/2026/one.html"))
    first = adapter.normalize_article(raw, "sports", "sports")
    second = replace(first, id="art_other", url="https://a.example.com/2026/two.html")

    assert store.save_article(first)["created"] is True
    duplicate = store.save_article(second)
    assert duplicate["duplicate"] is True
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_articles").fetchone()[0] == 1
        indexes = {row[1] for row in conn.execute("PRAGMA index_list('news_articles')").fetchall()}
        assert "idx_news_articles_content_hash" in indexes
