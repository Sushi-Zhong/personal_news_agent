from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from personal_news_agent.core.models import NormalizedArticle
from personal_news_agent.core.text import content_hash
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.topic_extraction import TopicExtractionService
from personal_news_agent.services.topic_extraction import TopicExtraction


class FakeTopicLLM:
    configured = True

    def __init__(self, invalid_id: bool = False):
        self.invalid_id = invalid_id
        self.calls = []

    async def structured(self, messages, schema_name, schema, model_key=None):
        self.calls.append(messages)
        import json

        payload = json.loads(messages[-1]["content"])
        recent = payload.get("candidate_events", payload.get("recent_topics", []))
        existing = "invented_topic" if self.invalid_id else (recent[0]["topic_id"] if recent else None)
        return {
            "category": payload["article"]["bound_category"],
            "subject": "国家足球队",
            "topic_name": "世界杯预选赛",
            "existing_topic_id": existing,
            "canonical_name": "世界杯预选赛",
            "action": "公布",
            "object": "国家队阵容",
            "temporal_scope": None,
            "event_stage": "announced",
            "stage_label": "阵容公布",
            "article_type": "official_statement",
            "event_summary": "球队公布最新阵容，并确认下一场世界杯预选赛的比赛安排。",
            "summary_decision": "revise",
            "summary_reason_code": "created" if existing is None else "fact_added",
            "updated_event_summary": "球队公布最新阵容，并确认下一场世界杯预选赛的比赛安排。",
            "keywords": ["世界杯", "阵容"],
            "classification_reason": "赛事、球队和阵容发布事项一致。",
            "confidence": 0.92,
        }


class UnconfiguredLLM:
    configured = False


def _article(article_id: str, title: str) -> NormalizedArticle:
    body = f"{title}。球队公布最新阵容，下一场比赛将在本周进行，这是用于主题抽取测试的正文。"
    return NormalizedArticle(
        id=article_id, source_id="test", section_key="sports", url=f"https://example.com/{article_id}.html",
        title=title, summary="", content=body, category="sports", published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc), source_priority=1, keywords=[], entities=[], content_hash=content_hash(body),
    )


def test_topic_extraction_creates_then_merges_recent_topic(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "国家队公布世界杯预选赛阵容"))
    store.save_article(_article("a2", "世界杯预选赛下一场赛程确认"))
    llm = FakeTopicLLM()
    service = TopicExtractionService(store, llm=llm)

    result = asyncio.run(service.process_pending(limit=2))

    assert result["processed"] == 2
    assert result["items"][0]["merged"] is False
    assert result["items"][1]["merged"] is True
    assert result["items"][0]["topic_id"] == result["items"][1]["topic_id"]
    assert result["items"][0]["subject"] == "国家足球队"
    assert len(store.list_recent_news_topics("sports")) == 1
    assert "recent_topics" in llm.calls[1][-1]["content"]
    with store.connect() as conn:
        subjects = [row[0] for row in conn.execute("SELECT subject FROM news_topic_articles ORDER BY article_id").fetchall()]
    assert subjects == ["国家足球队", "国家足球队"]


def test_topic_extraction_rejects_invented_topic_id(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "国家队公布世界杯预选赛阵容"))
    result = asyncio.run(TopicExtractionService(store, llm=FakeTopicLLM(invalid_id=True)).process_pending(limit=1))
    assert result["processed"] == 0
    assert "outside recent_topics" in result["errors"][0]["error"]
    assert len(store.list_unprocessed_topic_articles()) == 1
    with store.connect() as conn:
        audit = conn.execute(
            "SELECT decision, reason_code FROM news_event_classification_audits WHERE article_id = 'a1'"
        ).fetchone()
    assert dict(audit) == {"decision": "rejected", "reason_code": "classification_rejected"}


def test_topic_extraction_does_not_double_record_sqlite_lock_as_rejected(tmp_path, monkeypatch):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "国家队公布世界杯预选赛阵容"))
    rejection_calls = []

    def locked(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "apply_event_classification", locked)
    monkeypatch.setattr(
        store,
        "record_classification_rejection",
        lambda article_id, reason: rejection_calls.append((article_id, reason)),
    )

    result = asyncio.run(TopicExtractionService(store, llm=FakeTopicLLM()).process_pending(limit=1))

    assert result["processed"] == 0
    assert result["errors"][0]["error"] == "database is locked"
    assert rejection_calls == []


def test_topic_extraction_skips_without_configured_llm(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "尚待分类的单篇新闻"))
    result = asyncio.run(TopicExtractionService(store, llm=UnconfiguredLLM()).process_pending())
    assert result["status"] == "completed"
    assert result["reason"] == "llm_not_configured"
    assert result["processed"] == 1
    assert result["items"][0]["identity_status"] == "pending"
    assert result["items"][0]["event_key"].startswith("pending:article/v1:")


def test_pending_topic_articles_prioritize_latest_and_ignore_future_dates(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    now = datetime.now(timezone.utc)
    old = _article("old", "较早报道")
    latest = _article("latest", "最新报道")
    future = _article("future", "错误的未来日期报道")
    store.save_article(replace(old, published_at=now - timedelta(days=2)))
    store.save_article(replace(latest, published_at=now - timedelta(minutes=5)))
    store.save_article(replace(future, published_at=now + timedelta(days=30)))

    pending = store.list_unprocessed_topic_articles(limit=10)

    assert [item["id"] for item in pending] == ["latest", "old"]


def test_topic_extraction_schema_requires_every_output_field():
    schema = TopicExtraction.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False


def test_topic_extraction_confidence_threshold_is_configurable(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "国家队公布世界杯预选赛阵容"))

    result = asyncio.run(TopicExtractionService(store, llm=FakeTopicLLM(), confidence_threshold=0.95).process_pending(limit=1))

    assert result["items"][0]["identity_status"] == "pending"


def test_topic_extraction_can_merge_cross_category_candidate(tmp_path):
    store = NewsStore(tmp_path / "news.db"); store.init()
    store.save_article(_article("a1", "国家队公布世界杯预选赛阵容"))
    first = asyncio.run(TopicExtractionService(store, llm=FakeTopicLLM()).process_pending(limit=1))["items"][0]
    politics_article = replace(_article("a2", "足协官宣国家队新阵容"), category="politics", section_key="politics")
    store.save_article(politics_article)

    llm = FakeTopicLLM()
    second = asyncio.run(TopicExtractionService(store, llm=llm).process_pending(limit=1))["items"][0]

    assert second["topic_id"] == first["topic_id"]
    assert second["category"] == "politics"
    assert "candidate_events" in llm.calls[0][-1]["content"]


def test_topic_extraction_prompt_separates_event_stage_and_article_type():
    source = (Path(__file__).resolve().parents[1] / "personal_news_agent" / "prompts" / "topic_extraction.md").read_text(encoding="utf-8")
    assert "event_stage" in source
    assert "article_type" in source
    assert "candidate_events" in source
    assert "summary_decision" in source
    assert "updated_event_summary" in source
