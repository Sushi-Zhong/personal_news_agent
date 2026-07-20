from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from claude_code_backend.models import ChatRequest as LocalAgentChatRequest

from personal_news_agent.core.models import ReportResponse, SearchResult, TimeRange
from personal_news_agent.core.text import extract_entities, extract_keywords, stable_id, summarize
from personal_news_agent.services.search import UnifiedSearchService
from personal_news_agent.services.store import NewsStore


class ReportGenerationService:
    def __init__(self, store: NewsStore, search_service: UnifiedSearchService, local_agent: Any | None = None):
        self.store = store
        self.search_service = search_service
        self.local_agent = local_agent

    async def generate(self, user_id: str, topic: str, category_scope: list[str], time_range: str = "30d", report_type: str = "timeline_analysis") -> ReportResponse:
        if report_type == "daily_digest":
            return await self._generate_daily_digest(user_id, topic, category_scope, time_range, report_type)
        try:
            results = await self.search_service.search(topic, category_scope or None, None, None, max_results=12)
            articles = [self.store.get_article(item.article_id) for item in results if item.article_id]
            rows = [row for row in articles if row]
            combined = "\n".join(f"{row['title']}。{row.get('summary') or row.get('content') or ''}" for row in rows)
            keywords = extract_keywords(combined or topic, limit=10)
            entities = extract_entities(combined or topic, limit=10)
            timeline = self._timeline(rows)
            sections: dict[str, Any] = {
                "一、结论摘要": summarize(combined, 260) or f"{topic}暂无足够本地资料，需要接入外部搜索补充。",
                "二、事件背景": f"围绕“{topic}”检索到 {len(rows)} 篇本地/已入库文章，覆盖板块：{', '.join(category_scope) or '不限'}。",
                "三、关键时间线": timeline,
                "四、相关主体与关系": entities,
                "五、主要争议点/看点": keywords[:5],
                "六、不同来源的主要说法": [
                    {"source_id": row["source_id"], "title": row["title"], "summary": row.get("summary") or ""}
                    for row in rows[:6]
                ],
                "七、可能影响与后续观察指标": ["后续价格/产品动作", "多源报道是否交叉验证", "用户反馈和市场数据变化"],
                "八、来源列表与不确定性说明": "默认结果来自本地已抓取库；外部搜索 provider 未配置时，实时覆盖不足。",
            }
            report = {
                "topic": topic,
                "category_scope": category_scope,
                "report_type": report_type,
                "sections": sections,
                "timeline": timeline,
                "sources": [{"article_id": item.article_id, "source_id": item.source_id, "title": item.title, "url": item.url} for item in results],
            }
            report_id = self.store.save_report(user_id, topic, category_scope, report)
            self.store.log("report_generation", "ok", topic, {"report_id": report_id, "source_count": len(results), "timeline_count": len(timeline)})
            return ReportResponse(report_id=report_id, topic=topic, category_scope=category_scope, sections=sections, timeline=timeline, sources=report["sources"])
        except Exception as exc:
            self.store.log("report_generation", "error", topic, {"error": str(exc), "category_scope": category_scope})
            raise

    async def _generate_daily_digest(
        self,
        user_id: str,
        topic: str,
        category_scope: list[str],
        time_range: str,
        report_type: str,
    ) -> ReportResponse:
        try:
            profile = self.store.get_profile(user_id)
            categories = category_scope or list(profile.get("preferred_categories") or [])
            days = _days_from_range(time_range, default=1)
            results = await self._brief_results(topic, categories, days)
            rows = [self.store.get_article(item.article_id) for item in results if item.article_id]
            rows = [row for row in rows if row]
            evidence = _brief_evidence(results, rows)
            fallback = _fallback_brief_sections(topic, categories, profile, evidence)
            sections, agent_source = await self._agent_brief_sections(
                topic=topic,
                categories=categories,
                profile=profile,
                evidence=evidence,
                fallback=fallback,
                user_id=user_id,
            )
            sections["generation_source"] = agent_source
            sources = [
                {
                    "article_id": item.article_id,
                    "source_id": item.source_id,
                    "title": item.title,
                    "url": item.url,
                    "published_at": item.published_at.isoformat() if item.published_at else None,
                }
                for item in results
            ]
            report = {
                "topic": topic,
                "category_scope": categories,
                "report_type": report_type,
                "sections": sections,
                "timeline": [],
                "sources": sources,
            }
            report_id = self.store.save_report(user_id, topic, categories, report)
            self.store.log(
                "report_generation",
                "ok",
                topic,
                {"report_id": report_id, "source_count": len(results), "report_type": report_type, "agent_source": agent_source},
            )
            return ReportResponse(report_id=report_id, topic=topic, category_scope=categories, sections=sections, timeline=[], sources=sources)
        except Exception as exc:
            self.store.log("report_generation", "error", topic, {"error": str(exc), "category_scope": category_scope, "report_type": report_type})
            raise

    async def _brief_results(self, topic: str, categories: list[str], days: int) -> list[SearchResult]:
        broad_topics = {"", "今日资讯", "每日摘要", "今日简报"}
        if topic.strip() in broad_topics:
            rows = []
            if categories:
                for category in categories[:5]:
                    rows.extend(self.store.list_articles(category=category, limit=6, days=days))
            else:
                rows = self.store.list_articles(limit=18, days=days)
            return _search_results_from_rows(rows)[:18]
        return await self.search_service.search(
            topic,
            categories or None,
            None,
            TimeRange(days=days),
            max_results=18,
            include_remote=False,
        )

    async def _agent_brief_sections(
        self,
        topic: str,
        categories: list[str],
        profile: dict[str, Any],
        evidence: list[dict[str, Any]],
        fallback: dict[str, Any],
        user_id: str,
    ) -> tuple[dict[str, Any], str]:
        if not self.local_agent or not evidence:
            return fallback, "fallback"
        prompt = (
            "你是 Personal News Agent 的每日简报编辑。"
            "只能基于给定 evidence 写简报，不要添加 evidence 中没有的事实。"
            "输出必须是严格 JSON，不要 markdown，不要解释。"
            "JSON 字段：headline, summary, top_stories, why_it_matters, impact, watch_next, uncertainty, personalization_reason。"
            "top_stories 是数组，每项包含 title, summary, why_it_matters, source_index。"
            "why_it_matters、impact、watch_next 都是字符串数组。"
            "如果证据不足，明确写入 uncertainty。"
        )
        try:
            response = await self.local_agent.chat(
                LocalAgentChatRequest(
                    user_id=user_id,
                    message=prompt,
                    project_context={
                        "feature": "personal_news_agent_daily_brief",
                        "topic": topic,
                        "category_scope": categories,
                        "user_profile": _profile_context(profile),
                        "evidence": evidence,
                        "output_schema": list(fallback.keys()),
                    },
                    metadata={"purpose": "daily_brief_generation"},
                )
            )
        except Exception:
            return fallback, "fallback"
        if response.status != "ok":
            return fallback, "fallback"
        try:
            parsed = _decode_json_object(response.message.content)
            return _normalize_brief_sections(parsed, fallback, evidence), "local_agent"
        except Exception:
            return fallback, "fallback"

    def _timeline(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for row in sorted(rows, key=lambda item: item.get("published_at") or item.get("fetched_at") or ""):
            date_text = (row.get("published_at") or row.get("fetched_at") or datetime.utcnow().isoformat())[:10]
            text = f"{row['title']} {row.get('summary') or ''}"
            events.append(
                {
                    "date": date_text,
                    "event": row["title"],
                    "actors": extract_entities(text, limit=4),
                    "related_entities": extract_keywords(text, limit=4),
                    "source_article_ids": [row["id"]],
                    "confidence": 0.72,
                }
            )
        if not events:
            events.append(
                {
                    "date": datetime.utcnow().date().isoformat(),
                    "event": "暂无足够入库资料形成时间线",
                    "actors": [],
                    "related_entities": [],
                    "source_article_ids": [],
                    "confidence": 0.2,
                }
            )
        return events


def _days_from_range(value: str | None, default: int = 1) -> int:
    if value and value.endswith("d") and value[:-1].isdigit():
        return max(1, min(365, int(value[:-1])))
    return default


def _search_results_from_rows(rows: list[dict[str, Any]]) -> list[SearchResult]:
    results: list[SearchResult] = []
    seen: set[str] = set()
    for row in rows:
        article_id = row.get("id")
        if article_id in seen:
            continue
        seen.add(article_id)
        results.append(
            SearchResult(
                article_id=article_id,
                source_id=row.get("source_id") or "",
                title=row.get("title") or "",
                url=row.get("url") or "",
                summary=row.get("summary") or "",
                category=row.get("category") or "",
                published_at=_parse_datetime(row.get("published_at")),
                score=1.0,
                origin="local",
            )
        )
    return results


def _brief_evidence(results: list[SearchResult], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    row_by_id = {row.get("id"): row for row in rows}
    evidence = []
    for index, item in enumerate(results[:12], start=1):
        row = row_by_id.get(item.article_id) or {}
        content = row.get("content") or item.summary or ""
        evidence.append(
            {
                "index": index,
                "article_id": item.article_id,
                "source_id": item.source_id,
                "title": item.title,
                "url": item.url,
                "category": item.category,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": item.summary or summarize(content, 160),
                "content_excerpt": content[:600],
            }
        )
    return evidence


def _fallback_brief_sections(
    topic: str,
    categories: list[str],
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    combined = "\n".join(f"{item.get('title')}。{item.get('summary') or item.get('content_excerpt') or ''}" for item in evidence)
    summary = summarize(combined, 260) if combined else ""
    category_label = "、".join(categories) if categories else "不限板块"
    top_stories = [
        {
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "why_it_matters": "匹配当前主题或用户偏好，适合作为今日简报重点。",
            "source_index": item.get("index"),
        }
        for item in evidence[:5]
    ]
    keywords = extract_keywords(combined or topic, limit=8)
    sections = {
        "headline": f"今日简报：{topic}",
        "summary": summary or f"围绕“{topic}”暂无足够入库资料形成完整简报。",
        "top_stories": top_stories,
        "why_it_matters": keywords[:4] or ["需要更多来源交叉验证后再判断重要性。"],
        "impact": ["关注相关主体的后续动作、市场/用户反馈和多源报道是否一致。"],
        "watch_next": ["新增权威来源报道", "关键主体回应", "数据或政策变化"],
        "sources": [
            {
                "index": item.get("index"),
                "title": item.get("title"),
                "source_id": item.get("source_id"),
                "url": item.get("url"),
            }
            for item in evidence
        ],
        "uncertainty": "默认结果来自本地已抓取库；外部搜索未在本次简报中启用，实时覆盖可能不足。",
        "personalization_reason": f"按用户关注板块（{category_label}）和输出风格（{profile.get('output_style') or '简洁'}）整理。",
        "一、结论摘要": summary or f"围绕“{topic}”暂无足够入库资料形成完整简报。",
        "八、来源列表与不确定性说明": "默认结果来自本地已抓取库；外部搜索未在本次简报中启用，实时覆盖可能不足。",
    }
    return sections


def _normalize_brief_sections(parsed: dict[str, Any], fallback: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    sections = dict(fallback)
    for key in ("headline", "summary", "uncertainty", "personalization_reason"):
        value = parsed.get(key)
        if isinstance(value, str) and value.strip():
            sections[key] = value.strip()[:1200]
    for key in ("why_it_matters", "impact", "watch_next"):
        values = _clean_string_list(parsed.get(key), limit=6, max_length=180)
        if values:
            sections[key] = values
    stories = []
    for item in parsed.get("top_stories") or []:
        if not isinstance(item, dict):
            continue
        story = {
            "title": _clean_text(item.get("title"), 120),
            "summary": _clean_text(item.get("summary"), 260),
            "why_it_matters": _clean_text(item.get("why_it_matters"), 180),
            "source_index": item.get("source_index"),
        }
        if story["title"]:
            stories.append(story)
        if len(stories) >= 5:
            break
    if stories:
        sections["top_stories"] = stories
    sections["sources"] = [
        {
            "index": item.get("index"),
            "title": item.get("title"),
            "source_id": item.get("source_id"),
            "url": item.get("url"),
        }
        for item in evidence
    ]
    sections["一、结论摘要"] = sections["summary"]
    sections["八、来源列表与不确定性说明"] = sections["uncertainty"]
    return sections


def _decode_json_object(raw: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw or ""):
        if char != "{":
            continue
        try:
            payload, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    raise ValueError("brief agent did not return a JSON object")


def _profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "self_description": profile.get("self_description") or "",
        "interests": profile.get("interests") or [],
        "negative_interests": profile.get("negative_interests") or [],
        "preferred_categories": profile.get("preferred_categories") or [],
        "output_style": profile.get("output_style") or "简洁分析型",
    }


def _clean_string_list(value: Any, limit: int, max_length: int) -> list[str]:
    if not isinstance(value, list):
        return []
    cleaned = []
    for item in value:
        text = _clean_text(item, max_length)
        if text and text not in cleaned:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def _clean_text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:max_length]


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None
