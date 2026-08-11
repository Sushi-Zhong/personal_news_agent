from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import re

from personal_news_agent.core.models import TopicCluster
from personal_news_agent.core.text import extract_entities, extract_keywords, stable_id
from personal_news_agent.services.store import NewsStore


class EventDiscoveryService:
    def __init__(self, store: NewsStore):
        self.store = store

    def discover(self, category: str | None = None, days: int = 7, limit: int = 20) -> list[TopicCluster]:
        articles = self.store.list_articles(category=category, limit=200, days=days)
        buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for article in articles:
            text = f"{article['title']} {article.get('summary') or ''}"
            keywords = extract_keywords(text, limit=4)
            key = keywords[0] if keywords else article["title"][:8]
            buckets[(article["category"], key)].append(article)

        clusters: list[TopicCluster] = []
        for (cluster_category, key), rows in buckets.items():
            keywords = extract_keywords(" ".join(row["title"] for row in rows), limit=8)
            entities = extract_entities(" ".join((row["title"] or "") + " " + (row.get("summary") or "") for row in rows), limit=8)
            source_count = len({row["source_id"] for row in rows})
            article_count = len(rows)
            cohesion = min(1.0, len(rows) / max(1, len(keywords)))
            recency = 1.0
            source_priority = sum(1 / max(1, row.get("source_priority") or 5) for row in rows) / max(1, len(rows))
            hot_score = round(
                0.30 * min(article_count / 10, 1)
                + 0.20 * min(source_count / 5, 1)
                + 0.20 * cohesion
                + 0.15 * recency
                + 0.10 * 0.5
                + 0.05 * source_priority,
                4,
            )
            sorted_rows = sorted(rows, key=lambda row: row.get("published_at") or row.get("fetched_at") or "")
            cluster = TopicCluster(
                id=stable_id("evt", f"{cluster_category}:{key}:{','.join(row['id'] for row in rows)}"),
                title=_cluster_title(key, rows),
                category=cluster_category,
                keywords=keywords,
                entities=entities,
                article_ids=[row["id"] for row in rows],
                source_count=source_count,
                article_count=article_count,
                hot_score=hot_score,
                first_seen_at=_parse_dt(sorted_rows[0].get("published_at") or sorted_rows[0].get("fetched_at")),
                latest_seen_at=_parse_dt(sorted_rows[-1].get("published_at") or sorted_rows[-1].get("fetched_at")),
            )
            self.store.save_cluster(cluster)
            clusters.append(cluster)
        clusters.sort(key=lambda item: item.hot_score, reverse=True)
        return clusters[:limit]


def _cluster_title(key: str, rows: list[dict]) -> str:
    del key
    titles = [_clean_event_title(row.get("title") or "") for row in rows]
    titles = [title for title in titles if title]
    if not titles:
        return "热点事件"
    # Keyword extraction is intentionally permissive and can produce buckets such
    # as "10" or "流言板". Showing a real representative headline is more honest
    # and useful than presenting those weak tokens as a synthesized event name.
    return min(titles, key=lambda title: (abs(len(title) - 28), len(title)))


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


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)
