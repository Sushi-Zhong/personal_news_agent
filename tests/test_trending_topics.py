from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json

from personal_news_agent.core.models import NormalizedArticle
from personal_news_agent.core.text import content_hash
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.trending_topics import TrendingTopicBatch, TrendingTopicService, _normalize_batch_payload


class FakeTrendingLLM:
    configured = True

    def __init__(self, topics: list[dict]):
        self.topics = topics
        self.calls: list[list[dict]] = []

    async def structured(self, messages, schema_name, schema, model_key=None):
        self.calls.append(messages)
        assert schema_name == "trending_topic_batch"
        assert schema["additionalProperties"] is False
        return {"topics": self.topics}


class DisabledTrendingLLM:
    configured = False


def _article(article_id: str, source_id: str, title: str, category: str, age_hours: float) -> NormalizedArticle:
    now = datetime.now(timezone.utc)
    body = f"{title}。这是正文，但热点归并模型只应接收标题和系统统计所需字段。"
    return NormalizedArticle(
        id=article_id,
        source_id=source_id,
        section_key=category,
        url=f"https://{source_id}.example.com/{article_id}.html",
        title=title,
        summary=f"{title}摘要",
        content=body,
        category=category,
        published_at=now - timedelta(hours=age_hours),
        fetched_at=now - timedelta(hours=age_hours),
        source_priority=1,
        keywords=[],
        entities=[],
        content_hash=content_hash(body),
    )


def _store(tmp_path) -> NewsStore:
    store = NewsStore(tmp_path / "trending.db")
    store.init()
    store.save_profile(
        {
            "user_id": "sports_user",
            "interests": ["世界杯", "国家队"],
            "negative_interests": [],
            "preferred_categories": ["sports"],
            "preferred_sources": [],
            "output_style": "concise",
        }
    )
    return store


def test_trending_topics_use_title_window_and_system_heat_metrics(tmp_path):
    store = _store(tmp_path)
    for article in (
        _article("sport_1", "sina", "国家队公布世界杯预选赛最新阵容", "sports", 2.5),
        _article("sport_2", "sohu", "世界杯预选赛：国家队新阵容公布", "sports", 1.5),
        _article("sport_3", "cctv", "国家队确认世界杯预选赛参赛阵容", "sports", 0.5),
        _article("tech_1", "ithome", "某公司发布新款笔记本电脑", "tech", 0.4),
    ):
        store.save_article(article)

    llm = FakeTrendingLLM(
        [
            {
                "category": "sports",
                "title": "国家队公布世界杯预选赛阵容",
                "summary": "多家门户持续报道国家队公布世界杯预选赛参赛阵容。",
                "keywords": ["国家队", "世界杯预选赛", "阵容"],
                "article_ids": ["sport_1", "sport_2", "sport_3"],
                "confidence": 0.95,
            },
            {
                "category": "tech",
                "title": "某公司发布新款笔记本电脑",
                "summary": "一家门户报道某公司发布新款笔记本电脑。",
                "keywords": ["笔记本电脑"],
                "article_ids": ["tech_1"],
                "confidence": 0.8,
            },
        ]
    )
    service = TrendingTopicService(store, llm=llm)
    result = asyncio.run(service.recommend("sports_user", limit=2, window_hours=24, refresh_window_hours=6))

    assert result["generation_source"] == "llm"
    assert result["items"][0]["category"] == "sports"
    assert result["items"][0]["source_count"] == 3
    assert result["items"][0]["article_count"] == 3
    assert result["items"][0]["recent_update_count"] == 3
    assert result["items"][0]["hot_score"] > result["items"][1]["hot_score"]
    assert "匹配关注" in result["items"][0]["recommend_reason"]
    payload = json.loads(llm.calls[0][-1]["content"])
    assert {item["article_id"] for item in payload["articles"]} == {"sport_1", "sport_2", "sport_3", "tech_1"}
    assert all("content" not in item and "summary" not in item for item in payload["articles"])


def test_trending_topics_reject_invented_article_ids_and_fall_back(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("real_1", "sina", "真实的近期体育事件", "sports", 1))
    llm = FakeTrendingLLM(
        [
            {
                "category": "sports",
                "title": "模型编造的热点",
                "summary": "这条结果引用了输入中不存在的文章，因此不能采用。",
                "keywords": ["编造"],
                "article_ids": ["invented_article"],
                "confidence": 0.99,
            }
        ]
    )

    result = asyncio.run(TrendingTopicService(store, llm=llm).recommend("sports_user", limit=3))

    assert result["generation_source"] == "fallback"
    assert result["items"]
    assert result["items"][0]["article_ids"] == ["real_1"]
    assert all("invented_article" not in item["article_ids"] for item in result["items"])


def test_trending_topics_without_llm_returns_recent_article_fallback(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("recent_1", "cctv", "近期体育报道入口", "sports", 2))

    result = asyncio.run(TrendingTopicService(store, llm=DisabledTrendingLLM()).recommend("sports_user", limit=2))

    assert result["generation_source"] == "fallback"
    assert result["items"][0]["generation_source"] == "recent_article"
    assert result["items"][0]["topic_type"] == "recommended"


def test_trending_topics_empty_window_is_safe(tmp_path):
    store = _store(tmp_path)

    result = asyncio.run(TrendingTopicService(store, llm=DisabledTrendingLLM()).recommend("sports_user", limit=2))

    assert result["items"] == []
    assert result["article_count"] == 0


def test_trending_topic_batch_accepts_observed_compatible_envelope_alias():
    raw = {
        "trending_topics": [
            {
                "category": "sports",
                "title": "世界杯预选赛阵容更新",
                "summary": "多家门户报道世界杯预选赛阵容出现新的公开信息。",
                "article_ids": ["article_1"],
            }
        ]
    }

    parsed = TrendingTopicBatch.model_validate(_normalize_batch_payload(raw))

    assert parsed.topics[0].article_ids == ["article_1"]
    assert parsed.topics[0].keywords == []
    assert parsed.topics[0].confidence == 0.65
