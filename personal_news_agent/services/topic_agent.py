from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.tasks import ScheduledTaskService


SYSTEM_TOPIC_SEEDS = [
    {
        "title": "SpaceX IPO 传闻",
        "category_scope": ["tech", "economy"],
        "watch_keywords": ["SpaceX", "IPO", "Starlink", "上市"],
    },
    {
        "title": "2026 世界杯开幕",
        "category_scope": ["sports"],
        "watch_keywords": ["世界杯", "2026", "FIFA", "开幕"],
    },
]


class TopicAgentService:
    def __init__(
        self,
        store: NewsStore,
        tasks: ScheduledTaskService,
        topic_views: Any | None = None,
        native_ingestion: Any | None = None,
    ):
        self.store = store
        self.tasks = tasks
        self.topic_views = topic_views
        self.native_ingestion = native_ingestion

    def seed_system_topics(self) -> list[dict[str, Any]]:
        items = []
        for seed in SYSTEM_TOPIC_SEEDS:
            items.append(
                self.store.upsert_topic(
                    {
                        "user_id": "system",
                        "topic_type": "system",
                        "title": seed["title"],
                        "category_scope": seed["category_scope"],
                        "watch_keywords": seed["watch_keywords"],
                        "refresh_schedule": "*/30 * * * *",
                    }
                )
            )
        return items

    def list_topics(self, user_id: str = "default", topic_type: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.store.list_topics(user_id=user_id, topic_type=topic_type, limit=limit)

    async def create_topic(
        self,
        user_id: str,
        title: str,
        category_scope: list[str] | None = None,
        schedule: str = "*/20 * * * *",
        topic_type: str = "user",
        refresh_now: bool = True,
    ) -> dict[str, Any]:
        clean_title = _clean_title(title)
        if not clean_title:
            raise ValueError("topic title is required")
        if topic_type not in {"user", "system"}:
            raise ValueError("topic_type must be user or system")

        categories = category_scope or _infer_categories(clean_title)
        keywords = _keywords(clean_title)
        topic = self.store.upsert_topic(
            {
                "user_id": user_id if topic_type == "user" else "system",
                "topic_type": topic_type,
                "title": clean_title,
                "category_scope": categories,
                "watch_keywords": keywords,
                "refresh_schedule": schedule,
            }
        )

        task = self.store.get_task(topic["task_id"]) if topic.get("task_id") else None
        if topic_type == "user" and not task:
            task = self.tasks.create_task(
                {
                    "user_id": user_id,
                    "task_type": "topic_tracking",
                    "schedule": schedule,
                    "category_scope": categories,
                    "source_scope": [],
                    "topics": [clean_title],
                    "output_style": "事件线+关系网",
                    "delivery_channel": "in_app",
                }
            )
            topic = self.store.update_topic_task(topic["id"], task_id=task["id"]) or topic

        refresh = await self.refresh_topic(topic, refresh_now=refresh_now)
        if refresh.get("refreshed"):
            topic = self.store.update_topic_task(topic["id"], last_refresh_at=_now()) or topic
        return {"status": "ok", "topic": topic, "task": task, "refresh": refresh}

    async def create_topic_from_text(
        self,
        user_id: str,
        text: str,
        category_scope: list[str] | None = None,
        schedule: str = "*/20 * * * *",
        refresh_now: bool = True,
    ) -> dict[str, Any]:
        title = _extract_title(text)
        return await self.create_topic(
            user_id=user_id,
            title=title,
            category_scope=category_scope,
            schedule=schedule,
            topic_type="user",
            refresh_now=refresh_now,
        )

    async def maybe_create_topic_from_chat(self, user_id: str, message: str) -> dict[str, Any] | None:
        if not _looks_like_topic_request(message):
            return None
        return await self.create_topic_from_text(user_id=user_id, text=message, refresh_now=True)

    async def refresh_topic(self, topic: dict[str, Any], refresh_now: bool = True) -> dict[str, Any]:
        if not refresh_now:
            return {"refreshed": False, "reason": "disabled"}

        ingest_payload: dict[str, Any] | None = None
        topic_view: dict[str, Any] | None = None
        errors: list[str] = []
        title = topic["title"]
        categories = topic.get("category_scope") or None
        source_scope = topic.get("source_scope") or None

        if self.native_ingestion:
            try:
                ingest_payload = await self.native_ingestion.ingest(
                    query=title,
                    category_scope=categories,
                    source_scope=source_scope,
                    max_results=6,
                    fetch_articles=2,
                    follow_depth=0,
                    follow_limit_per_article=0,
                    max_sources=2,
                    request_timeout_seconds=3.0,
                )
            except Exception as exc:
                errors.append(f"ingest: {exc}")

        if self.topic_views:
            try:
                topic_view = await self.topic_views.build(
                    topic=title,
                    category_scope=categories,
                    source_scope=source_scope,
                    max_articles=12,
                )
            except Exception as exc:
                errors.append(f"topic_view: {exc}")

        return {
            "refreshed": bool(ingest_payload or topic_view),
            "ingest": ingest_payload,
            "topic_view": topic_view,
            "errors": errors,
        }


def _looks_like_topic_request(message: str) -> bool:
    compact = message.replace(" ", "")
    if "主题" not in compact:
        return False
    return any(token in compact for token in ("持续更新", "持续跟踪", "跟踪", "关注", "创建", "建立", "做一个", "整理"))


def _extract_title(text: str) -> str:
    patterns = [
        r"关于(.+?)的主题",
        r"做一个(.+?)主题",
        r"创建(.+?)主题",
        r"建立(.+?)主题",
        r"整理(.+?)主题",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return _clean_title(match.group(1))
    cleaned = text
    for token in ("我想", "帮我", "请你", "创建", "建立", "做一个", "整理", "主题", "并帮我", "持续更新", "持续跟踪", "持续关注"):
        cleaned = cleaned.replace(token, " ")
    return _clean_title(cleaned)


def _clean_title(value: str) -> str:
    cleaned = re.sub(r"[，。！？?！、]+", " ", value or "")
    cleaned = " ".join(cleaned.split())
    cleaned = re.sub(r"^(关于|一个|新的|长期)\s*", "", cleaned)
    if cleaned in {"主题", "专题", "专题任务"}:
        return ""
    return cleaned[:80].strip()


def _infer_categories(text: str) -> list[str]:
    hints = {
        "世界杯": "sports",
        "阿根廷": "sports",
        "FIFA": "sports",
        "NBA": "sports",
        "球队": "sports",
        "SpaceX": "tech",
        "IPO": "economy",
        "上市": "economy",
        "AI": "tech",
        "芯片": "tech",
        "俄乌": "politics",
        "战争": "politics",
        "农作物": "economy",
        "能源": "economy",
        "汽车": "auto",
        "新能源": "auto",
        "游戏": "game",
        "动漫": "anime",
    }
    lowered = text.lower()
    categories = [category for word, category in hints.items() if word.lower() in lowered]
    return sorted(set(categories))


def _keywords(title: str) -> list[str]:
    parts = re.split(r"[\s·:：,，/]+", title)
    keywords = [part for part in parts if len(part) >= 2]
    if title not in keywords:
        keywords.insert(0, title)
    return keywords[:8]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
