from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from personal_news_agent.core.models import ChatResponse, FocusObject, SearchResult, TimeRange
from personal_news_agent.services.chat_understanding import (
    extract_ordinal,
    infer_categories,
    query_from_message,
    time_range_from_message,
)
from personal_news_agent.services.content_moderation import ContentModerationError
from personal_news_agent.services.llm import LLMClient
from personal_news_agent.services.search import UnifiedSearchService
from personal_news_agent.services.store import NewsStore


class NewsChatService:
    def __init__(
        self,
        store: NewsStore,
        search_service: UnifiedSearchService,
        llm_client: LLMClient | None = None,
        native_ingestion: Any | None = None,
        deep_dive: Any | None = None,
        topic_views: Any | None = None,
        topic_agent: Any | None = None,
        content_moderation: Any | None = None,
    ):
        self.store = store
        self.search_service = search_service
        self.llm_client = llm_client or LLMClient()
        self.native_ingestion = native_ingestion
        self.deep_dive = deep_dive
        self.topic_views = topic_views
        self.topic_agent = topic_agent
        self.content_moderation = content_moderation

    async def chat(
        self,
        conversation_id: str | None,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
        user_id: str = "default",
    ) -> ChatResponse:
        conv_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        moderation_response = await self._moderate_query(conv_id, message)
        if moderation_response:
            self._save_response_turn(moderation_response, message)
            return moderation_response
        topic_response = await self._topic_agent_response(conv_id, user_id, message)
        ordinal = extract_ordinal(message) if not topic_response else None
        if topic_response:
            response = topic_response
        elif ordinal:
            response = await self._article_followup(conv_id, message, ordinal)
        elif use_llm:
            response = await self._research_chat(conv_id, message, topic, category_scope)
        else:
            response = await self._news_search(conv_id, message, topic, category_scope, use_llm)
        self._save_response_turn(response, message)
        return response

    async def chat_events(
        self,
        conversation_id: str | None,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
        user_id: str = "default",
    ) -> AsyncIterator[dict[str, Any]]:
        conv_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        yield {"type": "start", "conversation_id": conv_id, "message": "开始处理问题。"}
        moderation_response = await self._moderate_query(conv_id, message)
        if moderation_response:
            self._save_response_turn(moderation_response, message)
            yield {"type": "final", "response": moderation_response.model_dump(mode="json")}
            return
        topic_response = await self._topic_agent_response(conv_id, user_id, message)
        if topic_response:
            for item in topic_response.research_trace:
                yield {"type": "trace", "item": item}
            self._save_response_turn(topic_response, message)
            yield {"type": "final", "response": topic_response.model_dump(mode="json")}
            return
        ordinal = extract_ordinal(message)
        if ordinal or not use_llm:
            response = await (self._article_followup(conv_id, message, ordinal) if ordinal else self._news_search(conv_id, message, topic, category_scope, use_llm))
            self._save_response_turn(response, message)
            yield {"type": "final", "response": response.model_dump(mode="json")}
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit_trace(item: dict[str, Any]) -> None:
            await queue.put({"type": "trace", "item": item})

        async def run_pipeline() -> None:
            try:
                response = await self._research_chat(conv_id, message, topic, category_scope, emit_trace)
                self._save_response_turn(response, message)
                await queue.put({"type": "final", "response": response.model_dump(mode="json")})
            except Exception as exc:
                await queue.put({"type": "error", "message": str(exc)})

        task = asyncio.create_task(run_pipeline())
        try:
            while True:
                event = await queue.get()
                yield event
                if event["type"] in {"final", "error"}:
                    break
        finally:
            if not task.done():
                task.cancel()

    async def _topic_agent_response(self, conversation_id: str, user_id: str, message: str) -> ChatResponse | None:
        if not self.topic_agent:
            return None
        try:
            result = await self.topic_agent.maybe_create_topic_from_chat(user_id=user_id, message=message)
        except ValueError:
            return None
        if not result:
            return None
        topic = result["topic"]
        task = result.get("task")
        refresh = result.get("refresh") or {}
        ingest = refresh.get("ingest") or {}
        view = refresh.get("topic_view") or {}
        article_count = (view.get("build") or {}).get("article_count") or len(view.get("articles") or [])
        event_count = len(((view.get("event_line") or {}).get("items")) or [])
        answer = (
            f"已创建主题「{topic['title']}」，并保存为持续跟踪。\n\n"
            f"- 更新节奏：{topic.get('refresh_schedule') or '*/20 * * * *'}\n"
            f"- 抓取入库：发现 {ingest.get('discovered_count', 0)} 条，正文 {ingest.get('fetched_count', 0)} 条\n"
            f"- 专题视图：{article_count} 条证据，{event_count} 个事件节点\n\n"
            "后续可以直接问这个主题的最新变化、关键人物/球队/公司、影响链或让我生成报告。"
        )
        trace = [
            {"stage": "主题识别", "status": "completed", "message": f"识别为长期主题：{topic['title']}"},
            {"stage": "任务沉淀", "status": "completed", "message": f"已保存持续跟踪任务：{(task or {}).get('id') or '已存在'}"},
            {
                "stage": "抓取与视图",
                "status": "completed" if refresh.get("refreshed") else "skipped",
                "message": f"发现 {ingest.get('discovered_count', 0)} 条，专题证据 {article_count} 条。",
            },
        ]
        if refresh.get("errors"):
            trace.append({"stage": "刷新提示", "status": "warning", "message": "；".join(refresh["errors"][:2])})
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            markdown=answer,
            context_relation="topic_agent_created",
            focus_object=FocusObject(type="topic", target_id=topic["id"], text=topic["title"]),
            required_context_items=["topic_definition", "scheduled_task", "topic_refresh"],
            research_trace=trace,
            event_line=view.get("event_line"),
        )

    async def _moderate_query(self, conversation_id: str, message: str) -> ChatResponse | None:
        if not self.content_moderation or not getattr(self.content_moderation, "configured", False):
            return None
        try:
            result = await asyncio.to_thread(self.content_moderation.check_query_text, message)
        except ContentModerationError:
            return None
        if result.allowed:
            return None
        answer = "这条问题没有通过内容安全检测，请换一种问法后再试。"
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            markdown=answer,
            context_relation="query_moderation_blocked",
            focus_object=FocusObject(type="moderation", text=result.label or result.risk_level or "blocked"),
            required_context_items=["llm_query_moderation"],
            research_trace=[
                {
                    "stage": "输入安全检测",
                    "status": "blocked",
                    "message": result.description or result.message or "用户输入未通过内容安全检测。",
                    "label": result.label,
                    "risk_level": result.risk_level,
                    "request_id": result.request_id,
                }
            ],
        )



    def _save_response_turn(self, response: ChatResponse, message: str) -> str:
        return self.store.save_turn(
            response.conversation_id,
            message,
            response.answer,
            [item.model_dump(mode="json") for item in response.recommendations],
            response.focus_object.model_dump(mode="json") if response.focus_object else None,
        )

    async def _news_search(
        self,
        conversation_id: str,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
    ) -> ChatResponse:
        query = query_from_message(message, topic)
        categories = category_scope or infer_categories(message)
        results = await self.search_service.search(query=query, category_scope=categories, source_scope=None, time_range=None, max_results=20)
        results = _rank_for_chat(_enrich_from_store(self.store, results), message)[:8]
        if use_llm and self.llm_client.configured and results:
            try:
                answer = await self.llm_client.chat(_chat_messages(message, query, categories, results))
                context_relation = "topic_grounded_llm"
            except Exception as exc:
                answer = _grounded_answer(query, message, results, f"模型调用失败，已使用本地证据摘要：{exc}")
                context_relation = "topic_grounded_fallback"
        else:
            answer = _grounded_answer(query, message, results)
            context_relation = "topic_grounded"
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            context_relation=context_relation,
            focus_object=FocusObject(type="topic", text=query),
            required_context_items=["current_topic", "local_news_index", "retrieved_evidence"],
            recommendations=results,
        )

    async def _research_chat(
        self,
        conversation_id: str,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> ChatResponse:
        trace: list[dict[str, Any]] = []
        query = query_from_message(message, topic)
        categories = category_scope or infer_categories(message)
        time_range = time_range_from_message(message)
        await _add_trace(
            trace,
            {
                "stage": "理解问题",
                "status": "completed",
                "message": f"聚焦【{query}】"
                + (f"，限定近 {time_range.days} 天" if time_range else "")
                + (f"，分类 {', '.join(categories)}" if categories else ""),
            },
            on_trace,
        )

        await _add_trace(trace, {"stage": "本地召回", "status": "running", "message": "正在查询 ES 和本地新闻库。"}, on_trace)
        local_results = await self.search_service.search(query, categories, None, time_range, max_results=18, include_remote=False)
        local_results = _rank_for_chat(_filter_by_time(_enrich_from_store(self.store, local_results), time_range), message)
        await _add_trace(trace, {"stage": "本地召回", "status": "completed", "message": f"ES/本地库召回 {len(local_results)} 条候选。", "count": len(local_results)}, on_trace)

        ingest_payload: dict[str, Any] | None = None
        if self.native_ingestion:
            try:
                await _add_trace(trace, {"stage": "源搜索入库", "status": "running", "message": "正在搜索新闻源、抓取正文并写入 URL 管理。"}, on_trace)
                ingest_payload = await self.native_ingestion.ingest(
                    query=query,
                    category_scope=categories,
                    source_scope=None,
                    max_results=4,
                    fetch_articles=2,
                    follow_depth=0,
                    follow_limit_per_article=0,
                    max_sources=1,
                    request_timeout_seconds=3.0,
                )
                await _add_trace(
                    trace,
                    {
                        "stage": "源搜索入库",
                        "status": "completed",
                        "message": "完成源搜索、URL 入库、正文抓取和索引写入。",
                        "count": ingest_payload.get("discovered_count", 0),
                        "details": {
                            "discovered": ingest_payload.get("discovered_count", 0),
                            "fetched": ingest_payload.get("fetched_count", 0),
                            "indexed": ingest_payload.get("indexed_count", 0),
                            "mysql_ready": ingest_payload.get("mysql_ready"),
                            "elasticsearch_configured": ingest_payload.get("elasticsearch_configured"),
                        },
                    },
                    on_trace,
                )
            except Exception as exc:
                await _add_trace(trace, {"stage": "源搜索入库", "status": "error", "message": f"源搜索入库失败，继续使用已有证据：{exc}"}, on_trace)
        else:
            await _add_trace(trace, {"stage": "源搜索入库", "status": "skipped", "message": "当前服务未注入源搜索入库模块。"}, on_trace)

        await _add_trace(trace, {"stage": "阅读正文", "status": "running", "message": "正在基于新入库内容重新召回。"}, on_trace)
        refreshed_results = await self.search_service.search(query, categories, None, time_range, max_results=24, include_remote=False)
        refreshed_results = _rank_for_chat(_filter_by_time(_enrich_from_store(self.store, refreshed_results), time_range), message)
        await _add_trace(trace, {"stage": "阅读正文", "status": "completed", "message": f"抓取后重新召回 {len(refreshed_results)} 条候选，进入证据合并。", "count": len(refreshed_results)}, on_trace)

        expanded_queries: list[dict[str, Any]] = []
        expansion_results: list[SearchResult] = []
        if self.deep_dive:
            try:
                await _add_trace(trace, {"stage": "扩展搜索", "status": "running", "message": "正在生成垂直/横向扩展查询。"}, on_trace)
                deep_payload = await self.deep_dive.run(query, categories, None, rounds=1, breadth=4, include_remote=False)
                expanded_queries = list(deep_payload.get("expanded_queries") or [])[:6]
                for expansion in expanded_queries[:2]:
                    expansion_query = expansion.get("query")
                    if not expansion_query:
                        continue
                    results = await self.search_service.search(expansion_query, categories, None, time_range, max_results=5, include_remote=False)
                    expansion_results.extend(results)
                expansion_results = _rank_for_chat(_filter_by_time(_enrich_from_store(self.store, expansion_results), time_range), message)
                await _add_trace(
                    trace,
                    {
                        "stage": "扩展搜索",
                        "status": "completed",
                        "message": f"生成 {len(expanded_queries)} 个扩展查询，补充召回 {len(expansion_results)} 条候选。",
                        "count": len(expansion_results),
                    },
                    on_trace,
                )
            except Exception as exc:
                await _add_trace(trace, {"stage": "扩展搜索", "status": "error", "message": f"扩展搜索失败，继续合并已有证据：{exc}"}, on_trace)
        else:
            await _add_trace(trace, {"stage": "扩展搜索", "status": "skipped", "message": "当前服务未注入 deep dive 模块。"}, on_trace)

        merged_results = _merge_results([*refreshed_results, *local_results, *expansion_results])
        merged_results = _rank_for_chat(_filter_by_time(merged_results, time_range), message)[:12]
        evidence = _evidence_payload(self.store, merged_results)
        await _add_trace(trace, {"stage": "证据合并", "status": "completed", "message": f"去重后保留 {len(evidence)} 条可引用证据。", "count": len(evidence)}, on_trace)

        event_line = await self._event_line(query, categories, merged_results)
        if event_line and event_line.get("items"):
            await _add_trace(trace, {"stage": "事件线", "status": "completed", "message": f"生成 {len(event_line.get('items') or [])} 个时间节点。", "count": len(event_line.get("items") or [])}, on_trace)

        if self.llm_client.configured and evidence:
            try:
                await _add_trace(trace, {"stage": "生成回答", "status": "running", "message": "正在组织 markdown 回答。"}, on_trace)
                answer = await self.llm_client.chat(_research_messages(message, query, categories, time_range, evidence, expanded_queries, event_line, trace))
                context_relation = "research_pipeline_llm"
            except Exception as exc:
                answer = _research_fallback_answer(query, evidence, expanded_queries, event_line, f"模型调用失败，已使用本地证据摘要：{exc}")
                context_relation = "research_pipeline_fallback"
        else:
            answer = _research_fallback_answer(query, evidence, expanded_queries, event_line)
            context_relation = "research_pipeline_fallback" if evidence else "research_pipeline_empty"
        await _add_trace(trace, {"stage": "生成回答", "status": "completed", "message": "已生成 markdown 回答。"}, on_trace)

        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            markdown=answer,
            context_relation=context_relation,
            focus_object=FocusObject(type="topic", text=query),
            required_context_items=["research_pipeline", "source_search_ingest", "retrieved_evidence", "event_line"],
            recommendations=merged_results[:8],
            research_trace=trace,
            evidence=evidence,
            expanded_queries=expanded_queries,
            event_line=event_line,
        )

    async def _article_followup(self, conversation_id: str, message: str, ordinal: int) -> ChatResponse:
        last = self.store.last_turn(conversation_id)
        recommendations = (last or {}).get("recommendations") or []
        if ordinal < 1 or ordinal > len(recommendations):
            return ChatResponse(
                conversation_id=conversation_id,
                answer="上一轮没有对应序号的新闻，请先让我列出一组新闻。",
                context_relation="follow_up",
                focus_object=FocusObject(type="article", source_turn_id=(last or {}).get("id"), ordinal=ordinal),
                required_context_items=["previous_recommendation_list"],
            )
        selected = recommendations[ordinal - 1]
        article_id = selected.get("article_id")
        article = self.store.get_article(article_id) if article_id else None
        if not article:
            return ChatResponse(
                conversation_id=conversation_id,
                answer=f"第{ordinal}条来自外部搜索或尚未入库，当前只能基于标题和摘要说明：{selected.get('title')}。{selected.get('summary', '')}",
                context_relation="follow_up",
                focus_object=FocusObject(type="article", source_turn_id=(last or {}).get("id"), ordinal=ordinal, target_id=article_id),
                required_context_items=["previous_recommendation_list", "article_full_text"],
            )
        related = await self.search_service.search(article["title"], [article["category"]], None, None, max_results=3)
        answer = (
            f"第{ordinal}条是《{article['title']}》。\n"
            f"重要性：它属于{article['category']}板块的近期议题，摘要显示：{article.get('summary') or article.get('content', '')[:160]}\n"
            f"可以继续关注：相关主体、后续政策/产品动作、其他来源是否有交叉验证。"
        )
        return ChatResponse(
            conversation_id=conversation_id,
            answer=answer,
            context_relation="follow_up",
            focus_object=FocusObject(type="article", source_turn_id=(last or {}).get("id"), ordinal=ordinal, target_id=article_id),
            required_context_items=["previous_recommendation_list", "article_full_text", "related_articles"],
            recommendations=related,
        )

    async def _event_line(self, query: str, categories: list[str] | None, results: list[SearchResult]) -> dict[str, Any] | None:
        items = []
        for index, item in enumerate(results[:8], start=1):
            date = _date_text(item.published_at) or "未解析"
            items.append(
                {
                    "id": f"chat_evt_{index}",
                    "date": date,
                    "title": item.title,
                    "summary": item.summary[:180] if item.summary else "",
                    "stage": "证据",
                    "source_article_ids": [item.article_id] if item.article_id else [],
                }
            )
        if items:
            return {"view_type": "event_line", "items": items, "lanes": []}
        return await _maybe_build_topic_view(self.topic_views, query, categories)


async def _add_trace(
    trace: list[dict[str, Any]],
    item: dict[str, Any],
    on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> None:
    trace.append(item)
    if on_trace:
        await on_trace(item)


def _grounded_answer(query: str, message: str, results: list[SearchResult], prefix: str | None = None) -> str:
    if not results:
        return f"我现在没有在本地新闻库里找到【{query}】的可靠证据。可以先触发源搜索入库，再继续问我。"
    top = results[:5]
    dates = sorted({_date_text(item.published_at) for item in top if _date_text(item.published_at)})
    sources = "、".join(sorted({item.source_id for item in top}))
    bullets = []
    for item in top[:4]:
        summary = (item.summary or "").strip()
        detail = summary[:90] + ("…" if len(summary) > 90 else "")
        date = _date_text(item.published_at) or "未解析发布时间"
        bullets.append(f"- {item.title}（{item.source_id}，{date}）：{detail or '暂无摘要'}")
    lead = prefix + "\n\n" if prefix else ""
    return (
        f"{lead}围绕【{query}】，我现在基于 {len(results)} 条本地证据回答。\n"
        f"时间覆盖：{dates[0] + ' 至 ' + dates[-1] if dates else '部分来源未解析发布时间'}；来源：{sources or '本地库'}。\n\n"
        "当前主要变化：\n"
        + "\n".join(bullets)
        + "\n\n可以继续追问：赛事成绩线、商业/上市传闻线、舆论争议线，或让我把它升级为持续跟踪专题。"
    )


def _enrich_from_store(store: NewsStore, results: list[SearchResult]) -> list[SearchResult]:
    enriched = []
    for item in results:
        if not item.article_id:
            enriched.append(item)
            continue
        row = store.get_article(item.article_id)
        if not row:
            enriched.append(item)
            continue
        enriched.append(
            item.model_copy(
                update={
                    "title": row.get("title") or item.title,
                    "summary": row.get("summary") or item.summary,
                    "category": row.get("category") or item.category,
                    "published_at": _date_sort_value(row.get("published_at")),
                }
            )
        )
    return enriched


def _filter_by_time(results: list[SearchResult], time_range: TimeRange | None) -> list[SearchResult]:
    if not time_range:
        return results
    cutoff = datetime.now(timezone.utc) - timedelta(days=time_range.days)
    filtered = []
    unknown_dates = []
    for item in results:
        parsed = _date_sort_value(item.published_at)
        if not parsed:
            unknown_dates.append(item)
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed >= cutoff:
            filtered.append(item)
    return filtered or results[: min(len(results), 8)] or unknown_dates


def _merge_results(results: list[SearchResult]) -> list[SearchResult]:
    merged: list[SearchResult] = []
    seen: set[str] = set()
    for item in results:
        key = item.article_id or item.url
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


def _evidence_payload(store: NewsStore, results: list[SearchResult]) -> list[dict[str, Any]]:
    evidence = []
    for index, item in enumerate(results[:12], start=1):
        row = store.get_article(item.article_id) if item.article_id else None
        content = (row or {}).get("content") or item.summary or ""
        evidence.append(
            {
                "index": index,
                "article_id": item.article_id,
                "source_id": item.source_id,
                "title": item.title,
                "url": item.url,
                "category": item.category,
                "published_at": _date_text(item.published_at),
                "summary": item.summary or (content[:180] if content else ""),
                "content_excerpt": content[:700],
                "origin": item.origin,
                "score": item.score,
            }
        )
    return evidence


async def _maybe_build_topic_view(topic_views: Any, query: str, categories: list[str] | None) -> dict[str, Any] | None:
    if not topic_views:
        return None
    try:
        payload = await topic_views.build(query, categories, None, max_articles=12)
        event_line = payload.get("event_line") or {}
        items = list(event_line.get("items") or [])[:8]
        return {**event_line, "items": items}
    except Exception:
        return None


def _rank_for_chat(results: list[SearchResult], message: str) -> list[SearchResult]:
    seen = set()
    deduped = []
    for item in results:
        key = item.article_id or item.url
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    freshness_intent = any(token in message for token in ("今天", "最新", "新变化", "最近", "现在"))
    if not freshness_intent:
        return deduped
    return sorted(deduped, key=lambda item: (_date_sort_value(item.published_at) is not None, _date_sort_value(item.published_at) or datetime.min, item.score), reverse=True)


def _chat_messages(message: str, query: str, categories: list[str] | None, results: list[SearchResult]) -> list[dict[str, str]]:
    evidence = []
    for idx, item in enumerate(results[:8], start=1):
        date = _date_text(item.published_at) or "unknown"
        evidence.append(
            f"[{idx}] 标题：{item.title}\n来源：{item.source_id}\n日期：{date}\n摘要：{item.summary or ''}\n链接：{item.url}"
        )
    system = (
        "你是个人资讯助手。必须基于给定证据回答，不要编造。"
        "回答要像对话：先给结论，再给证据和可继续追问方向。"
        "如果证据不足，要明确说不足。"
    )
    user = (
        f"当前专题：{query}\n"
        f"分类范围：{', '.join(categories or []) or '未限定'}\n"
        f"用户问题：{message}\n\n"
        "证据：\n"
        + "\n\n".join(evidence)
        + "\n\n请用中文回答，控制在 500 字以内。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _research_messages(
    message: str,
    query: str,
    categories: list[str] | None,
    time_range: TimeRange | None,
    evidence: list[dict[str, Any]],
    expanded_queries: list[dict[str, Any]],
    event_line: dict[str, Any] | None,
    trace: list[dict[str, Any]],
) -> list[dict[str, str]]:
    evidence_text = []
    for item in evidence[:10]:
        evidence_text.append(
            f"[{item['index']}] {item['title']}\n"
            f"来源：{item['source_id']}｜日期：{item.get('published_at') or 'unknown'}｜origin：{item.get('origin')}\n"
            f"摘要：{item.get('summary') or ''}\n"
            f"正文片段：{item.get('content_excerpt') or ''}\n"
            f"链接：{item.get('url') or ''}"
        )
    expansion_text = "\n".join(
        f"- {item.get('query')}（{item.get('direction') or 'unknown'}：{item.get('rationale') or ''}）" for item in expanded_queries[:6]
    )
    timeline_text = "\n".join(
        f"- {item.get('date')}: {item.get('title')}｜{item.get('summary') or ''}" for item in (event_line or {}).get("items", [])[:8]
    )
    trace_text = "\n".join(f"- {item.get('stage')}: {item.get('message')}" for item in trace)
    system = (
        "你是个人资讯研究助手。必须严格基于证据回答，不要补充未在证据出现的事实。"
        "输出 Markdown，先给结论，再按时间/主题归纳，最后列不确定性和可追问方向。"
        "不要重复展示执行过程，执行过程会由系统单独渲染。"
        "如果证据不足，要明确指出不足，不要装作已经完整覆盖。"
    )
    user = (
        f"用户问题：{message}\n"
        f"研究主题：{query}\n"
        f"分类范围：{', '.join(categories or []) or '未限定'}\n"
        f"时间范围：近 {time_range.days} 天\n" if time_range else f"用户问题：{message}\n研究主题：{query}\n分类范围：{', '.join(categories or []) or '未限定'}\n时间范围：未限定\n"
    )
    user += (
        f"\n执行摘要：\n{trace_text}\n\n"
        f"扩展查询：\n{expansion_text or '无'}\n\n"
        f"事件线候选：\n{timeline_text or '无'}\n\n"
        "证据：\n"
        + "\n\n".join(evidence_text)
        + "\n\n请用中文输出，不超过 900 字，引用证据时用 [1] 这样的编号。"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _research_fallback_answer(
    query: str,
    evidence: list[dict[str, Any]],
    expanded_queries: list[dict[str, Any]],
    event_line: dict[str, Any] | None,
    prefix: str | None = None,
) -> str:
    if not evidence:
        return f"## {query}\n\n暂时没有召回到足够可靠的证据。可以先扩大来源、放宽时间范围，或补充更具体的关键词。"
    dates = [item.get("published_at") for item in evidence if item.get("published_at")]
    sources = sorted({item.get("source_id") for item in evidence if item.get("source_id")})
    lead = f"> {prefix}\n\n" if prefix else ""
    bullets = []
    for item in evidence[:5]:
        date = item.get("published_at") or "未解析日期"
        summary = (item.get("summary") or item.get("content_excerpt") or "")[:180]
        bullets.append(f"- [{item['index']}] {item['title']}（{item['source_id']}，{date}）：{summary or '暂无摘要'}")
    timeline = []
    for item in (event_line or {}).get("items", [])[:5]:
        timeline.append(f"- **{item.get('date') or '未解析'}**：{item.get('title')}{'｜' + item.get('summary', '')[:80] if item.get('summary') else ''}")
    expansions = [item.get("query") for item in expanded_queries[:4] if item.get("query")]
    return (
        f"{lead}## {query}\n\n"
        f"基于当前召回的 {len(evidence)} 条证据，覆盖来源：{', '.join(sources) or '本地库'}；"
        f"时间覆盖：{min(dates)} 至 {max(dates)}。\n\n"
        "### 主要线索\n"
        + "\n".join(bullets)
        + ("\n\n### 简版事件线\n" + "\n".join(timeline) if timeline else "")
        + ("\n\n### 已扩展的搜索方向\n" + "\n".join(f"- {item}" for item in expansions) if expansions else "")
        + "\n\n### 不确定性\n- 这是基于当前可抓取、可索引来源的阶段性结论；后续需要由 LLM 判断证据可信度、去重同源转载，并补充更强的一手来源。"
    )


def _date_text(value) -> str:
    parsed = _date_sort_value(value)
    return parsed.date().isoformat() if parsed else ""


def _date_sort_value(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None
