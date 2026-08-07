from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from personal_news_agent.core.models import RawArticle, RawArticleLink
from personal_news_agent.services.article_fetch import (
    _links_from_json_payload,
    _looks_like_article_url,
    _parse_published_datetime,
    _raw_article_from_toutiao_mobile_payload,
    canonicalize_url,
)
from personal_news_agent.services.crawl import CrawlScheduler, _interleave_by_source
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


class FailingArticleAdapter(FakeAdapter):
    async def fetch_article(self, url: str):
        raise TimeoutError()


class BrokenFirstLinkAdapter(FakeAdapter):
    async def crawl_section(self, section_key: str, limit: int):
        return [
            RawArticleLink(self.source.source_id, section_key, "https://a.example.com/2026/broken.html", "第一条已失效的新闻测试标题"),
            RawArticleLink(self.source.source_id, section_key, "https://a.example.com/2026/working.html", "第二条仍然可用的新闻测试标题"),
        ][:limit]

    async def fetch_article(self, url: str):
        if "broken" in url:
            raise TimeoutError("first link expired")
        return RawArticle(
            source_id=self.source.source_id,
            url=url,
            title=url,
            content="第二条新闻的正文内容足够长，证明单条失效不会阻塞整个栏目。",
        )


class StaleMySQLUrlStore:
    ready = True

    def list_due(self, **kwargs):
        return [
            {
                "id": "stale",
                "source_id": "removed_source",
                "section_key": "old",
                "category": "sports",
                "url": "https://old.example.com/",
            },
            {
                "id": "current",
                "source_id": "source_a",
                "section_key": "sports",
                "category": "sports",
                "url": "https://a.example.com/sports/",
            },
        ]

    def upsert_links(self, *args, **kwargs):
        return None

    def mark_fetch_ok(self, *args, **kwargs):
        return None

    def mark_fetch_error(self, *args, **kwargs):
        return None


class ExplodingUrlStore(StaleMySQLUrlStore):
    ready = False

    def upsert_links(self, *args, **kwargs):
        raise ConnectionError("mysql unavailable")

    def mark_fetch_ok(self, *args, **kwargs):
        raise ConnectionError("mysql unavailable")

    def mark_fetch_error(self, *args, **kwargs):
        raise ConnectionError("mysql unavailable")


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


def test_canonicalize_url_merges_toutiao_and_qq_article_variants():
    assert canonicalize_url("https://www.toutiao.com/group/1234567890123456/?utm_source=feed") == "https://www.toutiao.com/article/1234567890123456/"
    assert canonicalize_url("https://view.inews.qq.com/a/20260807A05O0F00?from=share") == "https://new.qq.com/rain/a/20260807A05O0F00"


def test_generic_json_feed_parser_handles_nested_portal_link_and_unix_time():
    payload = {
        "data": [
            {
                "title": "门户频道中的一条有效新闻标题",
                "publish_time": 1786068000,
                "link_info": {"share_url": "https://view.inews.qq.com/a/20260807A05O0F00"},
            },
            {"title": "不应当被当作文章的图片资源标题", "url": "https://inews.gtimg.com/example.jpg"},
        ]
    }

    links = _links_from_json_payload(payload, "qq_news", "tech", "https://i.news.qq.com/feed", ["qq.com"], 10)

    assert len(links) == 1
    assert links[0].url == "https://new.qq.com/rain/a/20260807A05O0F00"
    assert links[0].published_at == datetime.fromtimestamp(1786068000, tz=timezone.utc)


def test_toutiao_mobile_detail_parser_accepts_article_and_thread_content():
    article = _raw_article_from_toutiao_mobile_payload(
        "toutiao",
        "https://www.toutiao.com/article/1234567890123456/",
        json.dumps({"data": {"title": "今日头条测试正文标题", "content": "<p>第一段正文。</p><p>第二段正文。</p>", "publish_time": 1786068000}}),
    )
    thread = _raw_article_from_toutiao_mobile_payload(
        "toutiao",
        "https://www.toutiao.com/article/1234567890123457/",
        json.dumps({"data": {"thread": {"thread_base": {"content": "微头条正文也可以被柔性解析", "create_time": 1786068000}}}}),
    )

    assert article and article.content == "第一段正文。\n第二段正文。"
    assert article.published_at == _parse_published_datetime("1786068000")
    assert thread and thread.title == "微头条正文也可以被柔性解析"


def test_due_sections_are_interleaved_across_sources():
    items = [
        {"source_id": "qq", "section_key": "tech"},
        {"source_id": "qq", "section_key": "sports"},
        {"source_id": "toutiao", "section_key": "tech"},
        {"source_id": "toutiao", "section_key": "sports"},
    ]
    assert [item["source_id"] for item in _interleave_by_source(items)] == ["qq", "toutiao", "qq", "toutiao"]


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
        "fetch_errors": 0,
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


def test_mysql_due_plan_filters_rows_removed_from_current_registry(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    scheduler = CrawlScheduler(registry, store, url_store=StaleMySQLUrlStore(), adapter_factory=FakeAdapter)

    plan = scheduler.due_plan(category="sports", limit=10)

    assert plan["due_count"] == 1
    assert plan["sections"][0]["source_id"] == "source_a"


def test_article_fetch_timeout_is_visible_in_round_result(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    scheduler = CrawlScheduler(registry, store, adapter_factory=FailingArticleAdapter)

    result = asyncio.run(scheduler.crawl_due(category="sports", limit=10, fetch_articles=1))

    assert result["errors"] == 0
    assert result["fetch_errors"] == 1
    assert result["results"][0]["last_fetch_error"] == "TimeoutError"


def test_broken_first_link_does_not_block_next_article(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    scheduler = CrawlScheduler(registry, store, adapter_factory=BrokenFirstLinkAdapter)

    result = asyncio.run(scheduler.crawl_due(category="sports", limit=10, per_section_limit=5, fetch_articles=1, workers=1))

    assert result["saved_articles"] == 1
    assert result["fetch_errors"] == 1
    saved = store.list_articles(category="sports", limit=5)[0]
    assert saved["title"] == "第二条仍然可用的新闻测试标题"


def test_url_ledger_outage_does_not_block_article_persistence(tmp_path):
    registry = _registry(tmp_path / "sources.yaml")
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    scheduler = CrawlScheduler(registry, store, url_store=ExplodingUrlStore(), adapter_factory=FakeAdapter)

    result = asyncio.run(scheduler.crawl_category("sports", fetch_articles=1))

    assert result["results"][0]["saved"] == 1
    assert store.list_articles(category="sports", limit=5)[0]["source_id"] == "source_a"
    with store.connect() as conn:
        failures = conn.execute("SELECT COUNT(*) FROM operation_logs WHERE operation = 'crawl_url_store'").fetchone()[0]
    assert failures >= 2


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


def test_store_uses_wal_for_multi_process_server_workers(tmp_path):
    store = NewsStore(tmp_path / "news.db")
    store.init()

    with store.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
