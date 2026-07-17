from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, AsyncIterator, Awaitable, Callable
from uuid import uuid4

from claude_code_backend import LocalAgentService
from claude_code_backend.models import ChatRequest as LocalAgentChatRequest

from personal_news_agent.core.models import ChatResponse, FocusObject, SearchResult, TimeRange
from personal_news_agent.services.chat_understanding import (
    categories_for_message,
    extract_ordinal,
    is_contextual_followup,
    query_from_message,
    time_range_from_message,
)
from personal_news_agent.services.content_moderation import ContentModerationError
from personal_news_agent.services.llm import LLMClient
from personal_news_agent.services.search import (
    UnifiedSearchService,
    search_result_matches_subject,
    search_result_matches_terms,
)
from personal_news_agent.services.store import NewsStore


@dataclass(frozen=True)
class SearchQueryPlan:
    query: str
    primary_subject: str
    required_terms: list[str]
    keywords: list[str]
    source: str = "fallback"


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
        local_agent: LocalAgentService | None = None,
    ):
        self.store = store
        self.search_service = search_service
        self.llm_client = llm_client or LLMClient()
        self.native_ingestion = native_ingestion
        self.deep_dive = deep_dive
        self.topic_views = topic_views
        self.topic_agent = topic_agent
        self.content_moderation = content_moderation
        self.local_agent = local_agent or LocalAgentService()

    async def chat(
        self,
        conversation_id: str | None,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
        user_id: str = "default",
        allow_web_search: bool = False,
    ) -> ChatResponse:
        conv_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        topic, category_scope = self._resolve_conversation_context(conv_id, message, topic, category_scope, user_id)
        moderation_response = await self._moderate_query(conv_id, message)
        if moderation_response:
            self._save_response_turn(moderation_response, message, user_id, topic, category_scope)
            return moderation_response
        topic_response = await self._topic_agent_response(conv_id, user_id, message)
        ordinal = extract_ordinal(message) if not topic_response else None
        if topic_response:
            response = topic_response
        elif ordinal:
            response = await self._article_followup(conv_id, message, ordinal)
        elif use_llm:
            response = await self._research_chat(
                conv_id,
                message,
                topic,
                category_scope,
                user_id=user_id,
                allow_web_search=allow_web_search,
            )
        else:
            response = await self._news_search(
                conv_id,
                message,
                topic,
                category_scope,
                use_llm,
                user_id,
                allow_web_search,
            )
        self._save_response_turn(response, message, user_id, topic, category_scope)
        return response

    async def related_search(
        self,
        conversation_id: str | None,
        query: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        user_id: str = "default",
        max_queries: int = 5,
        allow_web_search: bool = False,
    ) -> ChatResponse:
        conv_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        message = f"/related {query}".strip()
        topic, category_scope = self._resolve_conversation_context(conv_id, query, topic, category_scope, user_id)
        base_query = query_from_message(query, topic)
        categories = categories_for_message(query, topic, category_scope)
        trace: list[dict[str, Any]] = [
            {
                "stage": "相关规划",
                "status": "running",
                "message": "正在使用本地 agent 生成相关检索词。",
            }
        ]
        related_queries, planner_source = await self._plan_related_queries(
            base_query,
            categories,
            user_id=user_id,
            max_queries=max_queries,
        )
        trace.append(
            {
                "stage": "相关规划",
                "status": "completed" if planner_source == "local_agent" else "fallback",
                "message": f"生成 {len(related_queries)} 个相关检索词。",
                "count": len(related_queries),
            }
        )

        grouped: list[dict[str, Any]] = []
        merged_results: list[SearchResult] = []
        for item in related_queries:
            related_query = item.get("query") or ""
            if not related_query:
                continue
            results = await self.search_service.search(
                query=related_query,
                category_scope=categories,
                source_scope=None,
                time_range=None,
                max_results=6,
                include_remote=allow_web_search,
            )
            ranked = _rank_for_chat(_enrich_from_store(self.store, results), related_query)[:5]
            grouped.append(
                {
                    "query": related_query,
                    "reason": item.get("reason") or "",
                    "relation_type": item.get("relation_type") or "other",
                    "relation_label": item.get("relation_label") or _related_relation_label(item.get("relation_type") or "other"),
                    "count": len(ranked),
                    "items": ranked,
                }
            )
            merged_results.extend(ranked)
        merged_results = _merge_results(merged_results)[:12]
        evidence = _evidence_payload(self.store, merged_results)
        trace.append(
            {
                "stage": "自动搜索",
                "status": "completed",
                "message": f"已完成 {len(grouped)} 组相关搜索，合并 {len(evidence)} 条证据。",
                "count": len(evidence),
            }
        )

        expanded_queries = [
            {
                "query": item.get("query") or "",
                "rationale": item.get("reason") or "",
                "relation_type": item.get("relation_type") or "other",
                "relation_label": item.get("relation_label") or _related_relation_label(item.get("relation_type") or "other"),
            }
            for item in related_queries
            if item.get("query")
        ]
        mind_map = _related_mind_map_payload(base_query, grouped, evidence, planner_source)
        answer = _related_search_answer(base_query, grouped, evidence, planner_source)
        response = ChatResponse(
            conversation_id=conv_id,
            answer=answer,
            markdown=answer,
            context_relation="related_search",
            topic=base_query,
            category_scope=categories or [],
            focus_object=FocusObject(type="topic", text=base_query),
            required_context_items=["local_agent_related_queries", "retrieved_evidence"],
            recommendations=merged_results[:8],
            research_trace=trace,
            evidence=evidence,
            expanded_queries=expanded_queries,
            mind_map=mind_map,
        )
        self._save_response_turn(response, message, user_id, topic, category_scope)
        return response

    async def chat_events(
        self,
        conversation_id: str | None,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
        user_id: str = "default",
        allow_web_search: bool = False,
    ) -> AsyncIterator[dict[str, Any]]:
        conv_id = conversation_id or f"conv_{uuid4().hex[:12]}"
        topic, category_scope = self._resolve_conversation_context(conv_id, message, topic, category_scope, user_id)
        yield {"type": "start", "conversation_id": conv_id, "message": "开始处理问题。"}
        moderation_response = await self._moderate_query(conv_id, message)
        if moderation_response:
            self._save_response_turn(moderation_response, message, user_id, topic, category_scope)
            yield {"type": "final", "response": moderation_response.model_dump(mode="json")}
            return
        topic_response = await self._topic_agent_response(conv_id, user_id, message)
        if topic_response:
            for item in topic_response.research_trace:
                yield {"type": "trace", "item": item}
            self._save_response_turn(topic_response, message, user_id, topic, category_scope)
            yield {"type": "final", "response": topic_response.model_dump(mode="json")}
            return
        ordinal = extract_ordinal(message)
        if ordinal or not use_llm:
            response = await (
                self._article_followup(conv_id, message, ordinal)
                if ordinal
                else self._news_search(
                    conv_id,
                    message,
                    topic,
                    category_scope,
                    use_llm,
                    user_id,
                    allow_web_search,
                )
            )
            self._save_response_turn(response, message, user_id, topic, category_scope)
            yield {"type": "final", "response": response.model_dump(mode="json")}
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def emit_trace(item: dict[str, Any]) -> None:
            await queue.put({"type": "trace", "item": item})

        async def run_pipeline() -> None:
            try:
                response = await self._research_chat(
                    conv_id,
                    message,
                    topic,
                    category_scope,
                    emit_trace,
                    user_id,
                    allow_web_search,
                )
                self._save_response_turn(response, message, user_id, topic, category_scope)
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
        is_create_command, inline_topic = _topic_create_request(message)
        if is_create_command and not inline_topic:
            answer = "好的，请发送要长期关注的主题。下一条消息会保存为新关注。"
            return ChatResponse(
                conversation_id=conversation_id,
                answer=answer,
                markdown=answer,
                context_relation="topic_create_pending",
                focus_object=FocusObject(type="topic_create_pending", text="pending"),
                required_context_items=["next_message_as_topic"],
                research_trace=[
                    {"stage": "关注创建", "status": "waiting", "message": "等待下一条消息作为长期关注主题。"}
                ],
            )
        if inline_topic:
            topic_text = inline_topic
        elif self._topic_create_is_pending(conversation_id, user_id):
            topic_text = message
        else:
            return None
        try:
            result = await self.topic_agent.create_topic_from_text(
                user_id=user_id,
                text=topic_text,
                refresh_now=True,
                conversation_id=conversation_id,
            )
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

    def _topic_create_is_pending(self, conversation_id: str, user_id: str) -> bool:
        last = self.store.last_turn(conversation_id, user_id=user_id)
        response = (last or {}).get("response") or {}
        return response.get("context_relation") == "topic_create_pending"

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



    def _resolve_conversation_context(
        self,
        conversation_id: str,
        message: str,
        topic: str | None,
        category_scope: list[str] | None,
        user_id: str,
    ) -> tuple[str | None, list[str] | None]:
        if not is_contextual_followup(message):
            return topic, category_scope
        turns = self.store.list_turns(conversation_id, user_id=user_id, limit=12)
        if not turns:
            turns = self.store.list_recent_turns(user_id=user_id, limit=6, exclude_conversation_id=conversation_id)
        if not turns:
            return topic, category_scope
        last = _last_turn_with_topic(turns)
        if not last:
            return topic, category_scope
        previous_topic = last.get("topic") or _focus_topic_text(last)
        previous_categories = last.get("category_scope") or category_scope
        return previous_topic or topic, previous_categories

    def _conversation_memory(
        self,
        conversation_id: str,
        user_id: str,
        current_limit: int = 6,
        recent_limit: int = 4,
    ) -> list[dict[str, Any]]:
        current_turns = self.store.list_turns(conversation_id, user_id=user_id, limit=current_limit)
        recent_turns = self.store.list_recent_turns(
            user_id=user_id,
            limit=recent_limit,
            exclude_conversation_id=conversation_id,
        )
        return _dedupe_turns([*recent_turns, *current_turns])[-(current_limit + recent_limit):]

    def _save_response_turn(
        self,
        response: ChatResponse,
        message: str,
        user_id: str,
        request_topic: str | None,
        request_categories: list[str] | None,
    ) -> str:
        resolved_topic = response.topic or (
            response.focus_object.text if response.focus_object and response.focus_object.type == "topic" else request_topic
        )
        resolved_categories = response.category_scope or request_categories or []
        turn_id = self.store.save_turn(
            response.conversation_id,
            message,
            response.answer,
            [item.model_dump(mode="json") for item in response.recommendations],
            response.focus_object.model_dump(mode="json") if response.focus_object else None,
            user_id=user_id,
            response=response.model_dump(mode="json"),
            topic=resolved_topic,
            category_scope=resolved_categories,
        )
        return turn_id

    async def _news_search(
        self,
        conversation_id: str,
        message: str,
        topic: str | None = None,
        category_scope: list[str] | None = None,
        use_llm: bool = False,
        user_id: str = "default",
        allow_web_search: bool = False,
    ) -> ChatResponse:
        query = query_from_message(message, topic)
        categories = categories_for_message(message, topic, category_scope)
        results = await self.search_service.search(
            query=query,
            category_scope=categories,
            source_scope=None,
            time_range=None,
            max_results=20,
            include_remote=allow_web_search,
        )
        results = _rank_for_chat(_enrich_from_store(self.store, results), message)[:8]
        if use_llm and self.llm_client.configured and results:
            try:
                history = self._conversation_memory(conversation_id, user_id)
                answer = await self.llm_client.chat(_chat_messages(message, query, categories, results, history))
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
            topic=query,
            category_scope=categories or [],
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
        user_id: str = "default",
        allow_web_search: bool = False,
    ) -> ChatResponse:
        trace: list[dict[str, Any]] = []
        query = query_from_message(message, topic)
        categories = categories_for_message(message, topic, category_scope)
        time_range = time_range_from_message(message)
        search_plan = await self._plan_search_query(message, topic, query, time_range, allow_web_search)
        search_query = search_plan.query
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
        if search_plan.source == "llm":
            await _add_trace(
                trace,
                {
                    "stage": "查询规划",
                    "status": "completed",
                    "message": f"联网检索式【{search_query}】，核心对象【{search_plan.primary_subject}】。",
                },
                on_trace,
            )

        await _add_trace(trace, {"stage": "本地召回", "status": "running", "message": "正在查询 ES 和本地新闻库。"}, on_trace)
        local_results = await self.search_service.search(search_query, categories, None, time_range, max_results=18, include_remote=False)
        local_results = _rank_for_chat(_filter_by_time(_enrich_from_store(self.store, local_results), time_range), message)
        await _add_trace(trace, {"stage": "本地召回", "status": "completed", "message": f"ES/本地库召回 {len(local_results)} 条候选。", "count": len(local_results)}, on_trace)

        ingest_payload: dict[str, Any] | None = None
        if self.native_ingestion and allow_web_search:
            try:
                await _add_trace(trace, {"stage": "源搜索入库", "status": "running", "message": "正在搜索新闻源、抓取正文并写入 URL 管理。"}, on_trace)
                ingest_payload = await self.native_ingestion.ingest(
                    query=search_query,
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
        elif self.native_ingestion:
            await _add_trace(
                trace,
                {
                    "stage": "源搜索入库",
                    "status": "skipped",
                    "message": "联网回答已关闭，跳过新闻源搜索和正文抓取。",
                },
                on_trace,
            )
        else:
            await _add_trace(trace, {"stage": "源搜索入库", "status": "skipped", "message": "当前服务未注入源搜索入库模块。"}, on_trace)

        await _add_trace(trace, {"stage": "阅读正文", "status": "running", "message": "正在基于新入库内容重新召回。"}, on_trace)
        refreshed_results = await self.search_service.search(search_query, categories, None, time_range, max_results=24, include_remote=False)
        refreshed_results = _rank_for_chat(_filter_by_time(_enrich_from_store(self.store, refreshed_results), time_range), message)
        await _add_trace(trace, {"stage": "阅读正文", "status": "completed", "message": f"抓取后重新召回 {len(refreshed_results)} 条候选，进入证据合并。", "count": len(refreshed_results)}, on_trace)

        external_results: list[SearchResult] = []
        freshness_terms = ("今天", "今日", "现在", "实时", "最新", "天气")
        should_search_external = allow_web_search and self.search_service.external_configured and (
            time_range is not None or len(refreshed_results) < 3 or any(term in message for term in freshness_terms)
        )
        if should_search_external:
            try:
                await _add_trace(trace, {"stage": "联网搜索", "status": "running", "message": "正在查询实时全网搜索。"}, on_trace)
                raw_external_results = await self.search_service.search_external(search_query, categories, None, max_results=8)
                external_results = raw_external_results
                if search_plan.required_terms:
                    external_results = [
                        item
                        for item in raw_external_results
                        if search_result_matches_terms(search_plan.required_terms, item)
                    ]
                await _add_trace(
                    trace,
                    {
                        "stage": "联网搜索",
                        "status": "completed",
                        "message": (
                            f"联网搜索返回 {len(raw_external_results)} 条，"
                            f"按核心对象保留 {len(external_results)} 条候选。"
                        ),
                        "count": len(external_results),
                    },
                    on_trace,
                )
            except Exception as exc:
                await _add_trace(trace, {"stage": "联网搜索", "status": "error", "message": f"联网搜索失败，继续使用已有证据：{exc}"}, on_trace)

        expanded_queries: list[dict[str, Any]] = []
        expansion_results: list[SearchResult] = []
        if self.deep_dive:
            try:
                await _add_trace(trace, {"stage": "扩展搜索", "status": "running", "message": "正在生成垂直/横向扩展查询。"}, on_trace)
                deep_payload = await self.deep_dive.run(search_query, categories, None, rounds=1, breadth=4, include_remote=False)
                expanded_queries = list(deep_payload.get("expanded_queries") or [])[:6]
                for expansion in expanded_queries[:2]:
                    expansion_query = expansion.get("query")
                    if not expansion_query:
                        continue
                    results = await self.search_service.search(expansion_query, categories, None, time_range, max_results=5, include_remote=False)
                    expansion_results.extend(results)
                if search_plan.required_terms:
                    expansion_results = [
                        item
                        for item in expansion_results
                        if search_result_matches_terms(search_plan.required_terms, item)
                    ]
                else:
                    expansion_results = [
                        item for item in expansion_results if search_result_matches_subject(search_query, item)
                    ]
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

        merged_results = _merge_results([*external_results, *refreshed_results, *local_results, *expansion_results])
        merged_results = _rank_for_chat(_filter_by_time(merged_results, time_range), message)[:12]
        evidence = _evidence_payload(self.store, merged_results)
        await _add_trace(trace, {"stage": "证据合并", "status": "completed", "message": f"去重后保留 {len(evidence)} 条可引用证据。", "count": len(evidence)}, on_trace)

        event_line = await self._event_line(query, categories, merged_results)
        if event_line and event_line.get("items"):
            await _add_trace(trace, {"stage": "事件线", "status": "completed", "message": f"生成 {len(event_line.get('items') or [])} 个时间节点。", "count": len(event_line.get("items") or [])}, on_trace)

        if self.llm_client.configured :#and evidence:
            try:
                await _add_trace(trace, {"stage": "生成回答", "status": "running", "message": "正在组织 markdown 回答。"}, on_trace)
                history = self._conversation_memory(conversation_id, user_id)
                answer = await self.llm_client.chat(_research_messages(message, query, categories, time_range, evidence, expanded_queries, event_line, trace, history))
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
            topic=query,
            category_scope=categories or [],
            focus_object=FocusObject(type="topic", text=query),
            required_context_items=["research_pipeline", "source_search_ingest", "retrieved_evidence", "event_line"],
            recommendations=merged_results[:8],
            research_trace=trace,
            evidence=evidence,
            expanded_queries=expanded_queries,
            event_line=event_line,
        )

    async def _plan_search_query(
        self,
        message: str,
        topic: str | None,
        fallback_query: str,
        time_range: TimeRange | None,
        allow_web_search: bool,
    ) -> SearchQueryPlan:
        fallback = SearchQueryPlan(
            query=fallback_query,
            primary_subject=fallback_query,
            required_terms=[],
            keywords=[],
        )
        if not allow_web_search or not self.llm_client.configured:
            return fallback

        system_prompt = (
            "你是搜索查询规划器，不要回答用户的问题。"
            "从用户原话和当前对话主题中识别真正需要检索的具体对象、关系或变化，"
            "不要把宽泛背景、起因或修饰词误当成核心对象。"
            "检索式应把最具体的目标对象放在前面，背景概念放在后面。"
            "只能依据输入改写，不得添加输入中没有依据的具体事实或实体。"
            "只返回一个严格 JSON 对象，字段为："
            '{"query":"适合搜索引擎的简洁检索式",'
            '"primary_subject":"最具体的核心对象或关系",'
            '"required_terms":["结果至少应出现其一的1到4个短主题词"],'
            '"keywords":["用于扩大召回的2到6个必要概念"]}。'
            "required_terms 必须描述目标对象本身，不能只描述背景事件。"
        )
        user_payload = {
            "message": message,
            "current_topic": topic or "",
            "normalized_query": fallback_query,
            "time_range_days": time_range.days if time_range else None,
        }
        try:
            raw = await self.llm_client.chat(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ]
            )
            payload = _decode_json_object(raw)
            planned_query = _clean_plan_text(payload.get("query"), 180)
            primary_subject = _clean_plan_text(payload.get("primary_subject"), 100)
            required_terms = _clean_plan_terms(payload.get("required_terms"), 4)
            keywords = _clean_plan_terms(payload.get("keywords"), 6)
            if not planned_query or not primary_subject:
                return fallback
            return SearchQueryPlan(
                query=planned_query,
                primary_subject=primary_subject,
                required_terms=required_terms,
                keywords=keywords,
                source="llm",
            )
        except Exception:
            return fallback

    async def _plan_related_queries(
        self,
        query: str,
        categories: list[str] | None,
        user_id: str,
        max_queries: int,
    ) -> tuple[list[dict[str, str]], str]:
        fallback = _fallback_related_queries(query, max_queries)
        prompt = (
            "你是个人资讯助手的相关搜索规划器。"
            "你的任务不是回答问题，而是为当前主题生成可以自动搜索的相关检索词。"
            "相关检索词应覆盖：最新进展、背景脉络、关键主体、影响/争议、可继续追踪线索。"
            "每个检索词必须标注它和当前主题的联系依据。"
            "不要写硬编码测试词，不要补充没有依据的具体事件。"
            "只返回严格 JSON，不要 markdown，不要解释。"
            "JSON 格式："
            '{"queries":[{"query":"简洁搜索词","relation_type":"latest|background|actor|impact|follow_up|other","reason":"为什么相关"}]}。'
            f"最多返回 {max_queries} 个。"
            "\n\n"
            f"当前主题：{query}\n"
            f"分类范围：{', '.join(categories or []) or '未限定'}"
        )
        try:
            response = await self.local_agent.chat(
                LocalAgentChatRequest(
                    user_id=user_id,
                    message=prompt,
                    project_context={
                        "feature": "personal_news_agent_related_search",
                        "topic": query,
                        "category_scope": categories or [],
                    },
                    metadata={"purpose": "related_search_query_planning"},
                )
            )
        except Exception:
            return fallback, "fallback"
        if response.status != "ok":
            return fallback, "fallback"
        parsed = _parse_related_queries(response.message.content, max_queries)
        if not parsed:
            return fallback, "fallback"
        return parsed, "local_agent"

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
    raise ValueError("search planner did not return a JSON object")


def _clean_plan_text(value: Any, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split()).strip()[:max_length]


def _clean_plan_terms(value: Any, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    terms: list[str] = []
    for item in value:
        term = _clean_plan_text(item, 40)
        if len(term) < 2 or term in terms:
            continue
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


def _fallback_related_queries(query: str, max_queries: int) -> list[dict[str, str]]:
    templates = [
        ("{query} 最新进展", "latest", "同一主题的近期变化。"),
        ("{query} 背景 脉络", "background", "补充主题的来龙去脉。"),
        ("{query} 关键主体", "actor", "寻找关联人物、机构、公司或地区。"),
        ("{query} 影响 争议", "impact", "关注影响面和争议点。"),
        ("{query} 后续 追踪", "follow_up", "发现可持续跟踪的线索。"),
    ]
    cleaned = " ".join((query or "").split()).strip() or "当前主题"
    return [
        {
            "query": template.format(query=cleaned),
            "relation_type": relation_type,
            "relation_label": _related_relation_label(relation_type),
            "reason": reason,
        }
        for template, relation_type, reason in templates[:max_queries]
    ]


def _parse_related_queries(raw: str, max_queries: int) -> list[dict[str, str]]:
    try:
        payload = _decode_json_object(raw)
    except Exception:
        return []
    source = payload.get("queries")
    if not isinstance(source, list):
        source = [payload] if payload.get("query") else []
    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in source:
        if not isinstance(item, dict):
            continue
        query = _clean_plan_text(item.get("query"), 120)
        relation_type = _clean_plan_text(item.get("relation_type") or item.get("dimension"), 40)
        reason = _clean_plan_text(item.get("reason") or item.get("rationale"), 160)
        relation_label = _clean_plan_text(item.get("relation_label") or item.get("label"), 40)
        key = query.casefold()
        if len(query) < 2 or key in seen:
            continue
        if not relation_type:
            relation_type = _infer_related_relation_type(query, reason)
        parsed.append(
            {
                "query": query,
                "relation_type": relation_type,
                "relation_label": relation_label or _related_relation_label(relation_type),
                "reason": reason,
            }
        )
        seen.add(key)
        if len(parsed) >= max_queries:
            break
    return parsed


def _infer_related_relation_type(query: str, reason: str) -> str:
    text = f"{query} {reason}"
    if any(term in text for term in ["最新", "近期", "变化", "进展"]):
        return "latest"
    if any(term in text for term in ["背景", "脉络", "历史", "来龙去脉"]):
        return "background"
    if any(term in text for term in ["主体", "人物", "机构", "公司", "地区"]):
        return "actor"
    if any(term in text for term in ["影响", "争议", "风险", "机会", "市场", "政策"]):
        return "impact"
    if any(term in text for term in ["后续", "追踪", "持续", "线索"]):
        return "follow_up"
    return "other"


def _related_relation_label(relation_type: str) -> str:
    labels = {
        "latest": "最新进展",
        "background": "背景脉络",
        "actor": "关键主体",
        "impact": "影响/争议",
        "follow_up": "后续追踪",
        "other": "语义相关",
    }
    return labels.get(relation_type, labels["other"])


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


def _related_search_answer(
    query: str,
    grouped: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    planner_source: str,
) -> str:
    planner_label = "local agent" if planner_source == "local_agent" else "fallback"
    lines = [
        f"我围绕【{query}】自动生成了 {len(grouped)} 组相关搜索，并完成检索。",
        f"规划来源：{planner_label}；合并证据：{len(evidence)} 条。",
        "",
        "## 相关思维导图",
        f"- 中心主题：{query}",
    ]
    for index, group in enumerate(grouped, start=1):
        relation_label = group.get("relation_label") or _related_relation_label(group.get("relation_type") or "other")
        reason = f"：{group['reason']}" if group.get("reason") else ""
        lines.append(f"- 分支 {index}｜{group.get('query', '')}（依据：{relation_label}{reason}）")
        items = group.get("items") or []
        for item_index, item in enumerate(items[:3], start=1):
            date = _date_text(item.published_at) or "日期未知"
            summary = " ".join((item.summary or "").split()).strip()
            excerpt = summary[:80] + ("…" if len(summary) > 80 else "")
            lines.append(f"- 小点 {index}.{item_index}｜{item.title}（{item.source_id}，{date}）：{excerpt or '暂无摘要'}")
    lines.append("")
    lines.append("## 证据索引")
    if not evidence:
        lines.append("暂时没有召回可引用证据。可以打开联网搜索或先做一次源搜索入库，再重新执行 /related。")
        return "\n".join(lines)
    for item in evidence[:8]:
        date = item.get("published_at") or "日期未知"
        summary = " ".join((item.get("summary") or "").split()).strip()
        excerpt = summary[:110] + ("…" if len(summary) > 110 else "")
        lines.append(f"- [{item.get('index')}] {item.get('title')}（{item.get('source_id')}，{date}）：{excerpt or '暂无摘要'}")
    lines.append("")
    lines.append("## 下一步")
    lines.append("可以从上面的某一组继续深挖，或把其中一个相关方向新增为关注。")
    return "\n".join(lines)


def _related_mind_map_payload(
    query: str,
    grouped: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    planner_source: str,
) -> dict[str, Any]:
    evidence_index_by_key = {
        item.get("article_id") or item.get("url"): item.get("index")
        for item in evidence
        if item.get("article_id") or item.get("url")
    }
    branches = []
    for group in grouped:
        relation_type = group.get("relation_type") or "other"
        relation_label = group.get("relation_label") or _related_relation_label(relation_type)
        edge_reason = group.get("reason") or f"按「{relation_label}」方向扩展当前主题。"
        points = []
        for item in (group.get("items") or [])[:4]:
            key = item.article_id or item.url
            summary = " ".join((item.summary or "").split()).strip()
            points.append(
                {
                    "title": item.title,
                    "summary": summary[:140] + ("…" if len(summary) > 140 else ""),
                    "source_id": item.source_id,
                    "date": _date_text(item.published_at),
                    "url": item.url,
                    "evidence_index": evidence_index_by_key.get(key),
                    "connection_reason": f"检索命中「{relation_label}」方向",
                }
            )
        branches.append(
            {
                "title": group.get("query") or "",
                "reason": group.get("reason") or "",
                "relation_type": relation_type,
                "relation_label": relation_label,
                "edge_reason": edge_reason,
                "count": group.get("count") or len(points),
                "points": points,
                "evidence_indices": [point["evidence_index"] for point in points if point.get("evidence_index")],
            }
        )
    return {
        "type": "related_mind_map",
        "topic": query,
        "planner_source": planner_source,
        "evidence_count": len(evidence),
        "branches": branches,
    }


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
            if item.origin == "external":
                unknown_dates.append(item)
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed >= cutoff:
            filtered.append(item)
    return [*filtered, *unknown_dates]


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
    return sorted(
        deduped,
        key=lambda item: (
            item.origin == "external",
            _date_sort_value(item.published_at) is not None,
            _date_sort_value(item.published_at) or datetime.min,
            item.score,
        ),
        reverse=True,
    )


def _chat_messages(
    message: str,
    query: str,
    categories: list[str] | None,
    results: list[SearchResult],
    history: list[dict[str, Any]] | None = None,
) -> list[dict[str, str]]:
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
        f"近期对话：\n{_conversation_history_text(history)}\n\n"
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
    history: list[dict[str, Any]] | None = None,
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
        f"近期对话：\n{_conversation_history_text(history)}\n\n"
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


def _dedupe_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for turn in turns:
        key = str(turn.get("id") or f"{turn.get('conversation_id')}:{turn.get('created_at')}:{turn.get('user_message')}")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(turn)
    return deduped


def _focus_topic_text(turn: dict[str, Any]) -> str | None:
    focus = turn.get("focus_object") or {}
    if focus.get("type") != "topic":
        return None
    return focus.get("text")


def _last_turn_with_topic(turns: list[dict[str, Any]]) -> dict[str, Any] | None:
    for turn in reversed(turns):
        if turn.get("topic") or _focus_topic_text(turn):
            return turn
    return None


def _conversation_history_text(history: list[dict[str, Any]] | None) -> str:
    if not history:
        return "无"
    lines = []
    for turn in history[-4:]:
        question = str(turn.get("user_message") or "").strip()[:240]
        answer = str(turn.get("assistant_answer") or "").strip()[:500]
        conversation_id = str(turn.get("conversation_id") or "").strip()
        prefix = f"[{conversation_id}] " if conversation_id else ""
        if question:
            lines.append(f"{prefix}用户：{question}")
        if answer:
            lines.append(f"{prefix}助手：{answer}")
    return "\n".join(lines) or "无"


def _topic_create_request(message: str) -> tuple[bool, str | None]:
    text = str(message or "").strip()
    compact = "".join(text.split())
    commands = ("创建一个新的长期专题任务", "创建新的长期专题任务", "新增长期专题任务")
    if compact in commands:
        return True, None
    for command in commands:
        if not text.startswith(command):
            continue
        suffix = text[len(command):].strip(" \t\r\n:：,，。-—")
        if suffix:
            return True, suffix
    return False, None


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
