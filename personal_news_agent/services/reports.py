from __future__ import annotations

import json
import re
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
            row_by_id = {row.get("id"): row for row in rows}
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
                    {
                        "article_id": row.get("id"),
                        "source_id": row["source_id"],
                        "title": row["title"],
                        "summary": row.get("summary") or "",
                        "content": row.get("content") or "",
                        "full_text": row.get("content") or row.get("summary") or "",
                    }
                    for row in rows[:6]
                ],
                "七、可能影响与后续观察指标": ["后续价格/产品动作", "多源报道是否交叉验证", "用户反馈和市场数据变化"],
                "八、来源列表与不确定性说明": "本报告主要基于已入库资料整理；若未开启实时联网搜索，可能遗漏最新报道。",
            }
            report = {
                "topic": topic,
                "category_scope": category_scope,
                "report_type": report_type,
                "sections": sections,
                "timeline": timeline,
                "sources": [
                    {
                        "article_id": item.article_id,
                        "source_id": item.source_id,
                        "title": item.title,
                        "url": item.url,
                        "summary": (row_by_id.get(item.article_id) or {}).get("summary") or item.summary,
                        "content": (row_by_id.get(item.article_id) or {}).get("content") or "",
                        "full_text": (
                            (row_by_id.get(item.article_id) or {}).get("content")
                            or (row_by_id.get(item.article_id) or {}).get("summary")
                            or item.summary
                        ),
                    }
                    for item in results
                ],
            }
            report_id = self.store.save_report(user_id, topic, category_scope, report)
            self.store.log("report_generation", "ok", topic, {"report_id": report_id, "source_count": len(results), "timeline_count": len(timeline)})
            return ReportResponse(report_id=report_id, topic=topic, category_scope=category_scope, sections=sections, timeline=timeline, sources=report["sources"])
        except Exception as exc:
            self.store.log("report_generation", "error", topic, {"error": str(exc), "category_scope": category_scope})
            raise

    async def generate_from_conversation(
        self,
        user_id: str,
        conversation_id: str,
        topic: str = "",
        category_scope: list[str] | None = None,
        limit: int = 40,
        strict_topic_filter: bool = True,
    ) -> ReportResponse:
        turns = [
            turn
            for turn in self.store.list_turns(conversation_id, user_id=user_id, limit=limit)
            if not str(turn.get("user_message") or "").strip().startswith("/report")
        ]
        visible_turns = [turn for turn in turns if _turn_context_relation(turn) != "query_moderation_blocked"]
        if not visible_turns and topic:
            return await self.generate(user_id, topic, category_scope or [], report_type="timeline_analysis")
        report_topic = _clean_topic_prefix(topic) or _conversation_topic(visible_turns) or "当前对话"
        scoped_turns = _filter_conversation_turns_by_topic(visible_turns, report_topic) if strict_topic_filter else visible_turns
        categories = category_scope or _conversation_categories(scoped_turns)
        evidence = _filter_conversation_evidence_by_topic(_conversation_evidence(scoped_turns), report_topic) if strict_topic_filter else _conversation_evidence(scoped_turns)
        combined = _conversation_combined_text(scoped_turns, evidence, report_topic)
        keywords = extract_keywords(combined, limit=10)
        entities = extract_entities(combined, limit=10)
        timeline = _conversation_timeline(scoped_turns, evidence)
        source_claims = _conversation_source_claims(evidence, scoped_turns)
        importance_points = _conversation_importance_points(report_topic, categories, evidence, scoped_turns)
        watch_points = _conversation_watch_points(scoped_turns, report_topic, evidence)
        sections: dict[str, Any] = {
            "一、结论摘要": _conversation_summary(scoped_turns, combined, report_topic),
            "二、事件背景": f"本报告只整理当前对话「{conversation_id}」里与“{report_topic}”相关的已出现内容，共覆盖 {len(scoped_turns)} 轮相关对话、{len(evidence)} 条已展示证据。",
            "三、关键时间线": timeline,
            "四、相关主体与关系": entities,
            "五、主要争议点/看点": importance_points,
            "六、不同来源的主要说法": source_claims,
            "七、可能影响与后续观察指标": watch_points,
            "八、来源列表与不确定性说明": "本报告只基于当前对话中已经出现且与主题相关的回答、证据和核查结果整理；报告阶段未新增检索、未补抓资料，明显偏离主题的内容已排除。",
        }
        sources = [
            {
                "article_id": item.get("article_id"),
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "summary": item.get("summary") or "",
                "content": item.get("content") or "",
                "full_text": item.get("full_text") or item.get("content") or item.get("summary") or "",
            }
            for item in evidence
        ]
        report = {
            "topic": report_topic,
            "category_scope": categories,
            "report_type": "conversation_summary",
            "conversation_id": conversation_id,
            "sections": sections,
            "timeline": timeline,
            "sources": sources,
        }
        report_id = self.store.save_report(user_id, report_topic, categories, report)
        self.store.log(
            "report_generation",
            "ok",
            report_topic,
            {
                "report_id": report_id,
                "source_count": len(sources),
                "timeline_count": len(timeline),
                "report_type": "conversation_summary",
            },
        )
        return ReportResponse(report_id=report_id, topic=report_topic, category_scope=categories, sections=sections, timeline=timeline, sources=sources)

    async def generate_brief_from_conversation(
        self,
        user_id: str,
        conversation_id: str,
        topic: str = "",
        category_scope: list[str] | None = None,
        time_range: str = "1d",
        limit: int = 40,
    ) -> ReportResponse:
        turns = [
            turn
            for turn in self.store.list_turns(conversation_id, user_id=user_id, limit=limit)
            if not str(turn.get("user_message") or "").strip().startswith("/brief")
        ]
        visible_turns = [turn for turn in turns if _turn_context_relation(turn) != "query_moderation_blocked"]
        if not visible_turns:
            return await self.generate(user_id, topic or "今日资讯", category_scope or [], time_range=time_range, report_type="daily_digest")
        report_topic = _clean_topic_prefix(topic) or _conversation_topic(visible_turns) or "今日资讯"
        scoped_turns = visible_turns
        categories = category_scope or _conversation_categories(scoped_turns)
        evidence = _conversation_evidence(scoped_turns)
        if not evidence:
            evidence = _brief_turn_evidence_from_conversation(scoped_turns)
        for index, item in enumerate(evidence, start=1):
            item["index"] = index

        profile = self.store.get_profile(user_id)
        fallback = _fallback_brief_sections(report_topic, categories, profile, evidence)
        fallback["summary"] = _brief_overview_summary(report_topic, evidence) or _conversation_summary(scoped_turns, _conversation_combined_text(scoped_turns, evidence, report_topic), report_topic)
        fallback["why_it_matters"] = _conversation_importance_points(report_topic, categories, evidence, scoped_turns)
        fallback["watch_next"] = _conversation_watch_points(scoped_turns, report_topic, evidence)
        fallback["uncertainty"] = "本次简报只整理当前对话中已经出现且未被排除在主题外的内容；简报阶段未新增检索。"
        fallback["一、结论摘要"] = fallback["summary"]
        fallback["八、来源列表与不确定性说明"] = fallback["uncertainty"]
        sections, agent_source = await self._agent_brief_sections(
            topic=report_topic,
            categories=categories,
            profile=profile,
            evidence=evidence,
            fallback=fallback,
            user_id=user_id,
        )
        sections["generation_source"] = f"conversation_{agent_source}"
        sources = [
            {
                "article_id": item.get("article_id"),
                "source_id": item.get("source_id"),
                "title": item.get("title"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "summary": item.get("summary") or "",
                "content": item.get("content") or "",
                "full_text": item.get("full_text") or item.get("content") or item.get("summary") or "",
            }
            for item in evidence
        ]
        report = {
            "topic": report_topic,
            "category_scope": categories,
            "report_type": "daily_digest",
            "conversation_id": conversation_id,
            "sections": sections,
            "timeline": [],
            "sources": sources,
        }
        report_id = self.store.save_report(user_id, report_topic, categories, report)
        self.store.log(
            "report_generation",
            "ok",
            report_topic,
            {
                "report_id": report_id,
                "source_count": len(sources),
                "report_type": "conversation_daily_digest",
                "agent_source": sections["generation_source"],
            },
        )
        return ReportResponse(report_id=report_id, topic=report_topic, category_scope=categories, sections=sections, timeline=[], sources=sources)

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
            "每条 top_stories.summary 必须是对该条资讯的 1 到 3 句总结，不要复制网页推荐流、下一篇标题或无关链接文字。"
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


def _turn_context_relation(turn: dict[str, Any]) -> str:
    response = turn.get("response") or {}
    return str(response.get("context_relation") or "")


def _clean_topic_prefix(value: str | None) -> str:
    topic = " ".join(str(value or "").split()).strip()
    prefixes = ("专题报告：", "专题报告:", "事实核查：", "事实核查:", "继续核查：", "继续核查:")
    changed = True
    while changed:
        changed = False
        for prefix in prefixes:
            if topic.startswith(prefix):
                topic = topic[len(prefix):].strip()
                changed = True
    return topic


def _conversation_topic(turns: list[dict[str, Any]]) -> str:
    for turn in turns:
        topic = _clean_topic_prefix(turn.get("topic"))
        if topic:
            return topic
        response = turn.get("response") or {}
        skill_data = ((response.get("skill_result") or {}).get("data") or {})
        topic = _clean_topic_prefix(skill_data.get("claim") or skill_data.get("topic"))
        if topic:
            return topic
        focus = response.get("focus_object") or turn.get("focus_object") or {}
        topic = _clean_topic_prefix(focus.get("text"))
        if topic:
            return topic
    for turn in turns:
        message = str(turn.get("user_message") or "").strip()
        if message and not message.startswith("/"):
            return _clean_topic_prefix(message)
    return ""


def _conversation_categories(turns: list[dict[str, Any]]) -> list[str]:
    categories: list[str] = []
    for turn in turns:
        for item in turn.get("category_scope") or []:
            if item and item not in categories:
                categories.append(item)
        response = turn.get("response") or {}
        for item in response.get("category_scope") or []:
            if item and item not in categories:
                categories.append(item)
    return categories


def _filter_conversation_turns_by_topic(turns: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
    if not turns or not topic or topic == "当前对话":
        return turns
    if _is_broad_report_topic(topic):
        return turns
    matched = [turn for turn in turns if _turn_matches_topic(turn, topic)]
    return matched or turns[-1:]


def _turn_matches_topic(turn: dict[str, Any], topic: str) -> bool:
    response = turn.get("response") or {}
    skill_data = ((response.get("skill_result") or {}).get("data") or {})
    focus = response.get("focus_object") or turn.get("focus_object") or {}
    text = " ".join(
        str(item or "")
        for item in (
            turn.get("topic"),
            skill_data.get("claim"),
            skill_data.get("topic"),
            focus.get("text"),
            turn.get("user_message"),
            turn.get("assistant_answer"),
        )
    )
    if _text_matches_topic(text, topic, min_hits=1):
        return True
    return any(
        _text_matches_topic(f"{item.get('title') or ''} {item.get('summary') or ''}", topic, min_hits=1)
        for item in _conversation_evidence([turn])
    )


def _filter_conversation_evidence_by_topic(evidence: list[dict[str, Any]], topic: str) -> list[dict[str, Any]]:
    if not evidence or not topic or topic == "当前对话":
        return evidence
    if _is_broad_report_topic(topic):
        return evidence
    anchor_terms = _topic_anchor_terms(topic)
    filtered = []
    for item in evidence:
        text = _evidence_topic_text(item)
        compact_text = _compact_for_topic(text)
        if anchor_terms and not any(term in compact_text for term in anchor_terms):
            continue
        if _text_matches_topic(text, topic, min_hits=2 if anchor_terms else 1):
            filtered.append(item)
    for index, item in enumerate(filtered, start=1):
        item["index"] = index
    return filtered


def _is_broad_report_topic(topic: str) -> bool:
    return _clean_topic_prefix(topic) in {"今日资讯", "今日简报", "每日摘要", "今日新闻"}


def _text_matches_topic(text: str, topic: str, min_hits: int = 1) -> bool:
    compact_topic = _compact_for_topic(topic)
    compact_text = _compact_for_topic(text)
    if not compact_topic:
        return True
    if compact_topic in compact_text or compact_text in compact_topic:
        return True
    if _same_gender_issue_family(compact_topic, compact_text):
        return True
    terms = _topic_match_terms(topic)
    if not terms:
        return True
    hits = sum(1 for term in terms if term in compact_text)
    return hits >= min_hits


def _same_gender_issue_family(compact_topic: str, compact_text: str) -> bool:
    if "重男轻女" in compact_topic:
        return any(term in compact_text for term in ("重男轻女", "男女", "性别", "女性", "男性", "女人", "女子", "女", "男", "人口比例", "出生性别比", "参政", "继承"))
    topic_terms = ("男女", "性别", "女性", "男性", "女子", "女权", "男权", "出生性别比")
    text_terms = (*topic_terms, "人口比例", "参政", "继承", "生育")
    return any(term in compact_topic for term in topic_terms) and any(term in compact_text for term in text_terms)


def _topic_match_terms(topic: str) -> list[str]:
    stopwords = {"新闻", "热点", "相关", "最新", "进展", "情况", "哪些", "什么", "怎么", "为什么", "继续", "核查", "报告", "2025", "2026", "亿元", "万元"}
    terms: list[str] = []
    for value in [*extract_entities(topic, limit=8), *extract_keywords(topic, limit=12)]:
        compact = _compact_for_topic(value)
        if len(compact) >= 2 and not compact.isdigit() and compact not in stopwords and compact not in terms:
            terms.append(compact)
    compact_topic = _compact_for_topic(topic)
    if _is_cjk_text(compact_topic):
        for size in (4, 3, 2):
            for index in range(0, max(0, len(compact_topic) - size + 1)):
                term = compact_topic[index : index + size]
                if term.isdigit() or term in stopwords or term in terms:
                    continue
                terms.append(term)
            if len(terms) >= 12:
                break
    return terms[:12]


def _compact_for_topic(value: Any) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _topic_anchor_terms(topic: str) -> list[str]:
    cleaned = _clean_topic_prefix(topic)
    primary = re.split(r"[：:，,。；;|｜\\-]", cleaned, maxsplit=1)[0].strip()
    primary = re.sub(r"^(中新网相关热点|2026相关热点|相关热点)\s*", "", primary).strip()
    primary = re.sub(r"(中新网|36氪|新浪财经|新华网)$", "", primary).strip(" -_")
    compact = _compact_for_topic(primary)
    if 2 <= len(compact) <= 12 and not compact.isdigit():
        return [compact]
    entities = []
    for item in extract_entities(cleaned, limit=4):
        compact_item = _compact_for_topic(item)
        if 2 <= len(compact_item) <= 12 and not compact_item.isdigit() and compact_item not in entities:
            entities.append(compact_item)
    return entities[:2]


def _evidence_topic_text(item: dict[str, Any]) -> str:
    title = _strip_source_tail(item.get("title") or "")
    return f"{title}。{item.get('summary') or ''}"


def _strip_source_tail(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"\s*[-—_]\s*(财经\s*[-—]\s*)?同花顺\s*$", "", text)
    text = re.sub(r"\s*[-—_]\s*(36氪|新浪财经|新华网|中国新闻网|中新网)\s*$", "", text)
    return text.strip()


def _is_cjk_text(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _conversation_evidence(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for turn in turns:
        response = turn.get("response") or {}
        skill_data = ((response.get("skill_result") or {}).get("data") or {})
        candidates: list[Any] = []
        candidates.extend(_brief_story_evidence_from_skill_data(skill_data))
        for key in ("supporting_evidence", "evidence", "sources"):
            value = skill_data.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        if isinstance(response.get("evidence"), list):
            candidates.extend(response["evidence"])
        if isinstance(response.get("recommendations"), list):
            candidates.extend(response["recommendations"])
        if isinstance(turn.get("recommendations"), list):
            candidates.extend(turn["recommendations"])
        for item in candidates:
            normalized = _normalize_conversation_evidence(item, turn)
            title = normalized.get("title")
            if not title:
                continue
            key = normalized.get("url") or normalized.get("article_id") or f"{normalized.get('source_id')}:{title}"
            if key in seen:
                continue
            seen.add(key)
            normalized["index"] = len(evidence) + 1
            evidence.append(normalized)
    return evidence


def _brief_turn_evidence_from_conversation(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for turn in turns:
        if _turn_is_structured_skill(turn):
            continue
        message = _clean_text(turn.get("user_message"), 180)
        answer = _clean_text(turn.get("assistant_answer"), 4000)
        if not message and not answer:
            continue
        rows.append(
            {
                "article_id": None,
                "source_id": "对话回答",
                "title": message or "对话内容",
                "url": "",
                "published_at": turn.get("created_at"),
                "category": (turn.get("category_scope") or [""])[0],
                "summary": _brief_story_summary(message, answer, answer),
                "content": answer,
                "full_text": answer,
            }
        )
    return rows


def _brief_story_evidence_from_skill_data(skill_data: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(skill_data, dict):
        return []
    sections = skill_data.get("sections") or {}
    if not isinstance(sections, dict):
        return []
    stories = sections.get("top_stories") or []
    sources = sections.get("sources") or skill_data.get("sources") or []
    source_by_index = {
        item.get("index"): item
        for item in sources
        if isinstance(item, dict) and item.get("index") is not None
    }
    rows: list[dict[str, Any]] = []
    for story in stories:
        if not isinstance(story, dict):
            continue
        source = source_by_index.get(story.get("source_index")) or {}
        rows.append(
            {
                "article_id": source.get("article_id"),
                "source_id": source.get("source_id") or "brief",
                "title": story.get("title") or source.get("title") or "",
                "url": source.get("url") or "",
                "published_at": source.get("published_at"),
                "category": source.get("category") or "",
                "summary": story.get("summary") or "",
            }
        )
    return rows


def _normalize_conversation_evidence(item: Any, turn: dict[str, Any]) -> dict[str, Any]:
    if hasattr(item, "model_dump"):
        item = item.model_dump(mode="json")
    if not isinstance(item, dict):
        return {}
    summary = (
        item.get("summary")
        or item.get("snippet")
        or item.get("content")
        or item.get("content_excerpt")
        or item.get("description")
        or item.get("quote")
        or ""
    )
    return {
        "article_id": item.get("article_id") or item.get("id"),
        "source_id": item.get("source_id") or item.get("source") or item.get("origin") or "对话证据",
        "title": _clean_text(item.get("title") or item.get("name") or "", 180),
        "url": item.get("url") or "",
        "published_at": item.get("published_at"),
        "category": item.get("category") or "",
        "summary": _clean_text(summary, 4000),
        "content": _clean_text(item.get("content") or "", 12000),
        "full_text": _clean_text(item.get("full_text") or item.get("content") or summary, 12000),
        "turn_id": turn.get("id"),
    }


def _conversation_combined_text(turns: list[dict[str, Any]], evidence: list[dict[str, Any]], topic: str) -> str:
    parts = [topic]
    for item in evidence:
        parts.append(f"{item.get('title') or ''}。{item.get('summary') or ''}")
    for turn in turns[-8:]:
        message = str(turn.get("user_message") or "").strip()
        answer = str(turn.get("assistant_answer") or "").strip()
        if message:
            parts.append(message)
        if answer and not _turn_is_structured_skill(turn):
            parts.append(answer[:1200])
    return "\n".join(part for part in parts if part)


def _turn_is_structured_skill(turn: dict[str, Any]) -> bool:
    response = turn.get("response") or {}
    skill_result = response.get("skill_result") or {}
    command = skill_result.get("command")
    return command in {"/brief", "/factcheck", "/report"}


def _conversation_summary(turns: list[dict[str, Any]], combined: str, topic: str) -> str:
    if not turns:
        return f"当前对话里还没有可用于生成“{topic}”报告的内容。"
    return summarize(combined, 300) or f"已根据当前对话中出现的内容整理“{topic}”。"


def _conversation_timeline(turns: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for item in evidence:
        date_text = _date_text(item.get("published_at")) or _date_text(_turn_created_at(turns, item.get("turn_id")))
        events.append(
            {
                "date": date_text or datetime.utcnow().date().isoformat(),
                "event": item.get("title") or "对话内证据",
                "actors": extract_entities(f"{item.get('title') or ''} {item.get('summary') or ''}", limit=4),
                "related_entities": extract_keywords(f"{item.get('title') or ''} {item.get('summary') or ''}", limit=4),
                "source_article_ids": [item.get("article_id")] if item.get("article_id") else [],
                "confidence": 0.7,
            }
        )
    if events:
        return events
    for turn in turns[-6:]:
        message = str(turn.get("user_message") or "").strip()
        if not message:
            continue
        events.append(
            {
                "date": _date_text(turn.get("created_at")) or datetime.utcnow().date().isoformat(),
                "event": _clean_text(message, 120),
                "actors": [],
                "related_entities": [],
                "source_article_ids": [],
                "confidence": 0.35,
            }
        )
    return events or [
        {
            "date": datetime.utcnow().date().isoformat(),
            "event": "当前对话尚无足够内容形成时间线",
            "actors": [],
            "related_entities": [],
            "source_article_ids": [],
            "confidence": 0.2,
        }
    ]


def _conversation_source_claims(evidence: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if evidence:
        return [
            {
                "source_id": item.get("source_id") or "对话证据",
                "title": item.get("title") or "",
                "summary": item.get("summary") or "",
            }
            for item in evidence
        ]
    claims: list[dict[str, Any]] = []
    for turn in turns[-6:]:
        if _turn_is_structured_skill(turn):
            continue
        answer = _clean_text(turn.get("assistant_answer"), 260)
        if answer:
            claims.append({"source_id": "对话回答", "title": _clean_text(turn.get("user_message"), 120), "summary": answer})
    return claims


def _conversation_importance_points(topic: str, categories: list[str], evidence: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[str]:
    if not evidence and not turns:
        return ["当前对话内可用资料不足，需要先补充与主题直接相关的证据，才能判断重要性。"]
    compact = _compact_for_topic(topic)
    points: list[str] = []
    if any(term in compact for term in ("分红", "派息", "利润分配", "现金红利")) or "economy" in categories:
        points.append("这类信息会影响投资者对公司现金回报、盈利质量和股东权益安排的判断。")
        points.append("需要区分公司自身公告、媒体转述和同类公司案例，避免把其他公司的分红信息误当成该主题证据。")
    elif "auto" in categories:
        points.append("该主题可能影响消费者决策、企业营销动作和行业竞争预期。")
    elif "tech" in categories:
        points.append("该主题可能影响产品路线、产业链合作或监管与市场预期。")
    elif "politics" in categories:
        points.append("该主题可能影响政策执行、公共讨论和相关主体后续回应。")
    if evidence:
        points.append(f"当前对话已展示 {len(evidence)} 条与主题相关的证据，后续应继续确认来源是否为原始公告或权威媒体。")
    else:
        points.append("当前对话没有筛出足够直接证据，报告应把结论保持为初步整理而非确定判断。")
    return _dedupe_preserve_order(points)[:4]


def _conversation_watch_points(turns: list[dict[str, Any]], topic: str, evidence: list[dict[str, Any]]) -> list[str]:
    points: list[str] = []
    for turn in turns:
        response = turn.get("response") or {}
        skill_data = ((response.get("skill_result") or {}).get("data") or {})
        for key in ("next_checks", "missing_evidence"):
            value = skill_data.get(key)
            if not isinstance(value, list):
                continue
            for item in value:
                text = _clean_text(item, 160)
                if text and text not in points:
                    points.append(text)
                if len(points) >= 6:
                    return points
    defaults = [
        f"查找“{topic}”对应主体发布的原始公告或正式声明。",
        "对比后续新增报道是否与当前已展示证据一致，尤其留意标题和正文是否指向同一主体。",
        "关注关键时间点后的市场反应、主体回应或补充披露。",
    ]
    if evidence:
        defaults.append("复核证据来源中是否混入同名平台、同类案例或泛行业文章。")
    return _dedupe_preserve_order(points or defaults)[:4]


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _clean_text(value, 220)
        if text and text not in result:
            result.append(text)
    return result


def _turn_created_at(turns: list[dict[str, Any]], turn_id: Any) -> Any:
    for turn in turns:
        if turn.get("id") == turn_id:
            return turn.get("created_at")
    return None


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:10]


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
        summary = _brief_story_summary(item.title, item.summary or "", content)
        evidence.append(
            {
                "index": index,
                "article_id": item.article_id,
                "source_id": item.source_id,
                "title": item.title,
                "url": item.url,
                "category": item.category,
                "published_at": item.published_at.isoformat() if item.published_at else None,
                "summary": summary,
                "content_excerpt": _brief_clean_excerpt(content),
            }
        )
    return evidence


def _fallback_brief_sections(
    topic: str,
    categories: list[str],
    profile: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    summary = _brief_overview_summary(topic, evidence)
    category_label = "、".join(categories) if categories else "不限板块"
    top_stories = [
        {
            "title": item.get("title") or "",
            "summary": _brief_story_summary(item.get("title") or "", item.get("summary") or "", item.get("content_excerpt") or ""),
            "why_it_matters": _brief_story_reason(item),
            "source_index": item.get("index"),
        }
        for item in evidence[:5]
    ]
    sections = {
        "headline": f"今日简报：{topic}",
        "summary": summary or f"围绕“{topic}”暂无足够入库资料形成完整简报。",
        "top_stories": top_stories,
        "why_it_matters": _brief_why_it_matters(topic, categories, evidence),
        "impact": _brief_impact(categories, evidence),
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
        values = [item for item in values if not _brief_noise_text(item)]
        if values:
            sections[key] = values
    stories = []
    evidence_by_index = {item.get("index"): item for item in evidence}
    for item in parsed.get("top_stories") or []:
        if not isinstance(item, dict):
            continue
        source_item = evidence_by_index.get(item.get("source_index")) or {}
        reason = _clean_text(item.get("why_it_matters"), 180)
        if _brief_noise_text(reason):
            reason = _brief_story_reason(source_item)
        story = {
            "title": _clean_text(item.get("title"), 120),
            "summary": _brief_story_summary(
                item.get("title") or source_item.get("title") or "",
                item.get("summary") or source_item.get("summary") or "",
                source_item.get("content_excerpt") or "",
            ),
            "why_it_matters": reason,
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


def _brief_story_reason(item: dict[str, Any]) -> str:
    category = item.get("category") or ""
    category_reasons = {
        "tech": "涉及技术产品或产业链变化，适合观察后续商业化与监管反馈。",
        "auto": "涉及汽车消费或产业动作，适合观察价格、销量和企业后续回应。",
        "economy": "涉及市场或经营数据变化，适合观察后续政策与企业经营影响。",
        "game": "涉及游戏内容、发行或用户反馈，适合观察平台热度和口碑变化。",
        "sports": "涉及赛事或体育产业动态，适合观察赛果、组织方回应和商业影响。",
        "politics": "涉及公共政策或治理议题，适合观察后续执行细则和官方回应。",
        "entertainment": "涉及文娱内容或文化活动，适合观察传播效果和公众反馈。",
    }
    return category_reasons.get(category, "这条资讯包含明确主体和后续变量，适合纳入今日简报继续观察。")


def _brief_overview_summary(topic: str, evidence: list[dict[str, Any]]) -> str:
    sentences: list[str] = []
    for item in evidence[:5]:
        summary = _brief_story_summary(item.get("title") or "", item.get("summary") or "", item.get("content_excerpt") or item.get("content") or "")
        for sentence in _brief_summary_sentences(summary):
            if sentence and sentence not in sentences:
                sentences.append(sentence)
            if len(sentences) >= 3:
                return " ".join(sentences)
    return f"围绕“{topic}”暂无足够入库资料形成完整简报。" if topic else ""


def _brief_story_summary(title: str, summary: str, content: str) -> str:
    text = _brief_clean_excerpt(summary or content)
    if not text:
        return f"{title}：暂无足够正文信息，需等待后续来源补充。" if title else ""
    sentences = _brief_summary_sentences(text)
    return " ".join(sentences[:3]).strip()


def _brief_clean_excerpt(value: Any) -> str:
    text = _clean_text(value, 4000)
    if not text:
        return ""
    text = re.sub(r"上一篇[:：]?.*", "", text)
    text = re.sub(r"下一篇[:：]?.*", "", text)
    text = re.sub(r"责任编辑[:：].*", "", text)
    for marker in _BRIEF_RECOMMENDATION_MARKERS:
        index = text.find(marker)
        if index > 20:
            text = text[:index].strip()
            break
    return text.strip()


def _brief_summary_sentences(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return []
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])\s*", normalized) if part.strip()]
    if not parts:
        parts = [normalized]
    sentences = []
    for part in parts:
        if _brief_noise_text(part):
            continue
        if _brief_boilerplate_sentence(part):
            continue
        sentences.append(part)
        if len(sentences) >= 3:
            break
    return sentences or parts[:1]


def _brief_boilerplate_sentence(value: str) -> bool:
    text = str(value or "").strip(" #*·。 ：:\t\r\n")
    if not text:
        return True
    if text in {"信息资讯", "来源", "分享", "上一篇", "下一篇"}:
        return True
    return bool(re.fullmatch(r"来源[:：]?.{0,30}(时间[:：]?.*)?", text))


_BRIEF_RECOMMENDATION_MARKERS = (
    "当AI拥有",
    "APP借钱套路调查",
    "太阳花",
    "台风",
    "更多精彩",
    "相关推荐",
    "相关报道",
    "延伸阅读",
)


def _brief_why_it_matters(topic: str, categories: list[str], evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return ["当前对话内可用资料不足，需要补充权威来源后再判断重要性。"]
    category_label = "、".join(categories or sorted({item.get("category") for item in evidence if item.get("category")})) or "多个板块"
    points = [f"本次简报汇总了 {len(evidence)} 条已入库资讯，覆盖{category_label}，便于快速把握今日信息面。"]
    themes = _brief_clean_themes(evidence)
    if themes:
        points.append("高频议题集中在：" + "、".join(themes[:4]) + "。")
    points.append("这些资讯仍主要来自本地已抓取库，后续需要用新增报道和主体回应交叉验证。")
    return points


def _brief_impact(categories: list[str], evidence: list[dict[str, Any]]) -> list[str]:
    if not evidence:
        return ["暂无足够证据判断影响。"]
    impacts = []
    if "tech" in categories:
        impacts.append("可能影响技术产品、产业链合作和企业研发节奏。")
    if "auto" in categories:
        impacts.append("可能影响汽车消费决策、价格预期和企业营销动作。")
    if "game" in categories:
        impacts.append("可能影响内容热度、玩家反馈和平台分发。")
    if not impacts:
        impacts.append("关注相关主体的后续动作、市场/用户反馈和多源报道是否一致。")
    return impacts


def _brief_clean_themes(evidence: list[dict[str, Any]]) -> list[str]:
    combined = "\n".join(f"{item.get('title') or ''} {item.get('summary') or ''}" for item in evidence)
    banned = {
        "36",
        "-36",
        "36氪",
        "kr36",
        "游民星空",
        "gamersky",
        "gamersky.com",
        "com",
        "重要性",
        "匹配当前主题或用户偏好",
        "适合作为今日简报重点",
    }
    themes = []
    for item in extract_keywords(combined, limit=16):
        cleaned = str(item).strip(" -—_·|：:，,。")
        if len(cleaned) < 2 or cleaned.lower() in banned or cleaned in banned:
            continue
        if cleaned not in themes:
            themes.append(cleaned)
    return themes[:6]


def _brief_noise_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    banned_fragments = (
        "匹配当前主题或用户偏好",
        "适合作为今日简报重点",
        "gamersky",
        "gamerSky.com".lower(),
        "网络游戏新闻",
        "_17173.com",
    )
    if any(fragment in text for fragment in banned_fragments):
        return True
    cleaned = text.strip(" -—_·|：:，,。")
    return cleaned in {"36", "-36", "36氪", "kr36", "com"}


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
