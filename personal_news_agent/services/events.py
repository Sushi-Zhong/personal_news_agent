from __future__ import annotations

import re

from personal_news_agent.core.models import TopicCluster
from personal_news_agent.services.store import NewsStore


class EventDiscoveryService:
    """Build and read event projections without deciding article ownership."""

    def __init__(self, store: NewsStore):
        self.store = store

    def rebuild(self, category: str | None = None, limit: int = 100) -> list[TopicCluster]:
        self.store.prune_event_projections()
        events = self.store.list_canonical_events(category=category, limit=limit)
        for event in events:
            self.store.refresh_event_projection(event["id"])
        return [_as_cluster(event) for event in self.store.list_canonical_events(category=category, limit=limit)]

    def list_events(self, category: str | None = None, limit: int = 20) -> list[dict]:
        # Reading the canonical tables is also the no-write fallback when a
        # projection refresh is delayed or the projection tables are empty.
        return self.store.list_canonical_events(category=category, limit=limit)

    def discover(self, category: str | None = None, days: int = 7, limit: int = 20) -> list[TopicCluster]:
        del days
        return self.rebuild(category=category, limit=limit)


def _as_cluster(event: dict) -> TopicCluster:
    return TopicCluster(
        id=event["id"],
        title=event["title"],
        category=event["category"],
        keywords=event["keywords"],
        entities=[],
        article_ids=event["article_ids"],
        source_count=event["source_count"],
        article_count=event["article_count"],
        hot_score=event["hot_score"],
        first_seen_at=event.get("first_seen_at"),
        latest_seen_at=event.get("last_seen_at"),
    )


def _cluster_title(key: str, rows: list[dict]) -> str:
    del key
    titles = [_clean_event_title(row.get("title") or "") for row in rows]
    titles = [title for title in titles if title]
    return min(titles, key=lambda title: (abs(len(title) - 28), len(title))) if titles else "热点事件"


def _clean_event_title(value: str) -> str:
    title = re.sub(r"\s+", " ", str(value or "")).strip()
    title = re.sub(r"^[\[【](?:流言板|速报|快讯)[\]】]\s*", "", title)
    title = re.sub(
        r"\s*[-_—|]\s*(?:中新网|腾讯新闻|新浪(?:新闻|财经)?|搜狐(?:新闻)?|今日头条|虎扑|游民星空|中华网(?:军事)?|界面新闻)\s*$",
        "",
        title,
        flags=re.IGNORECASE,
    )
    return title[:90].strip()
