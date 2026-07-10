from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from personal_news_agent.api.schemas import (
    ChatRequest,
    DeepDiveRequest,
    DueCrawlRequest,
    DueTasksRequest,
    FeedbackRequest,
    LoginRequest,
    NativeSearchIngestRequest,
    NotificationReadRequest,
    OnboardingRequest,
    ProfileRequest,
    RegisterRequest,
    ReportRequest,
    SearchRequest,
    TaskRequest,
    TopicCreateRequest,
    TopicViewRequest,
    mask_mobile,
    parse_range,
)
from personal_news_agent.config import Settings
from personal_news_agent.core.categories import CATEGORIES
from personal_news_agent.services.auth import AuthError


def register_routes(app: FastAPI, services: dict[str, Any], static_dir: Path, settings: Settings) -> None:
    registry = services["registry"]
    store = services["store"]
    url_store = services["url_store"]
    search_index = services["search_index"]
    search_service = services["search"]
    events = services["events"]

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(static_dir / "home.html")

    @app.get("/web")
    async def web_app() -> FileResponse:
        return FileResponse(static_dir / "home.html")

    @app.get("/auth")
    async def auth_app() -> FileResponse:
        return FileResponse(static_dir / "auth.html")

    @app.get("/mobile")
    async def mobile_app() -> FileResponse:
        return FileResponse(static_dir / "home.html")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "categories": CATEGORIES, "source_count": len(registry.all_sources())}

    @app.get("/api/models")
    async def models() -> dict[str, Any]:
        return {
            "items": services["model_options"](),
            "default_model": settings.llm_default_model,
            "endpoint_configured": bool(settings.llm_endpoint),
        }

    @app.get("/api/sources")
    async def sources(category: str | None = None) -> dict[str, Any]:
        try:
            selected = registry.get_sources_by_category(category) if category else registry.all_sources()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "items": [
                {
                    "source_id": source.source_id,
                    "name": source.name,
                    "categories": source.categories,
                    "tags": source.tags,
                    "region": source.region,
                    "language": source.language,
                    "credibility": source.credibility,
                    "crawl_interval_minutes": source.crawl_interval_minutes,
                    "crawl_enabled": source.crawl_enabled,
                    "search_enabled": source.search_enabled,
                    "sections": [section.__dict__ for section in source.sections],
                }
                for source in selected
            ]
        }

    @app.get("/api/sources/summary")
    async def source_summary() -> dict[str, Any]:
        return registry.source_summary() | {"inventory": store.list_source_inventory()}

    @app.get("/api/crawl/due")
    async def crawl_due_plan(category: str | None = None, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
        return services["crawl"].due_plan(category=category, limit=limit)

    @app.get("/api/crawl/urls/due")
    async def crawl_due_urls(category: str | None = None, limit: int = Query(default=50, ge=1, le=200), url_type: str | None = "article") -> dict[str, Any]:
        return {"items": url_store.list_due(category=category, limit=limit, url_type=url_type), "mysql_ready": url_store.ready}

    @app.post("/api/crawl/due")
    async def crawl_due(payload: DueCrawlRequest) -> dict[str, Any]:
        return await services["crawl"].crawl_due(
            category=payload.category,
            limit=payload.limit,
            per_section_limit=payload.per_section_limit,
            fetch_articles=payload.fetch_articles,
        )

    @app.get("/api/feed")
    async def feed(category: str | None = None, limit: int = Query(default=20, ge=1, le=100), user_id: str = "default") -> dict[str, Any]:
        return {"items": services["feed"].feed(user_id=user_id, category=category, limit=limit)}

    @app.post("/api/profile")
    async def save_profile(payload: ProfileRequest) -> dict[str, Any]:
        store.save_profile(payload.model_dump())
        return {"status": "ok", "profile": store.get_profile(payload.user_id)}

    @app.get("/api/profile")
    async def get_profile(user_id: str = "default") -> dict[str, Any]:
        user = store.get_user(user_id)
        profile = store.get_profile(user_id)
        return {
            "user": {
                "id": user["id"],
                "username": user.get("username"),
                "display_name": user.get("display_name"),
                "mobile": mask_mobile(user.get("mobile") or ""),
                "realname_verified": bool(user.get("realname_verified")),
                "realname_provider": user.get("realname_provider"),
                "assistant_prompt": user.get("assistant_prompt"),
            }
            if user
            else None,
            "profile": profile,
        }

    @app.post("/api/auth/register")
    async def register(payload: RegisterRequest) -> dict[str, Any]:
        try:
            return services["auth"].register_with_realname(
                username=payload.username,
                password=payload.password,
                confirm_password=payload.confirm_password,
                real_name=payload.real_name,
                mobile=payload.mobile,
                id_card=payload.id_card,
            )
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest) -> dict[str, Any]:
        try:
            return services["auth"].login(payload.username, payload.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/api/auth/realname/status")
    async def realname_status() -> dict[str, Any]:
        return services["auth"].realname.status()

    @app.get("/api/onboarding/options")
    async def onboarding_options() -> dict[str, Any]:
        return services["onboarding"].options()

    @app.post("/api/onboarding/complete")
    async def onboarding_complete(payload: OnboardingRequest) -> dict[str, Any]:
        try:
            return services["onboarding"].complete(payload.user_id, payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/auth/wechat/status")
    async def wechat_status() -> dict[str, Any]:
        return services["auth"].wechat_status()

    @app.get("/api/auth/wechat/login-url")
    async def wechat_login_url(mode: str | None = None, state_param: str | None = Query(default=None, alias="state"), redirect_uri: str | None = None) -> dict[str, Any]:
        try:
            return services["auth"].wechat_login_url(mode=mode, state=state_param, redirect_uri=redirect_uri)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/auth/wechat/callback")
    async def wechat_callback(code: str, state_param: str | None = Query(default=None, alias="state")) -> dict[str, Any]:
        try:
            return await services["auth"].wechat_callback(code=code, state=state_param)
        except AuthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/feedback")
    async def feedback(payload: FeedbackRequest) -> dict[str, Any]:
        store.save_feedback(payload.user_id, payload.target_type, payload.target_id, payload.feedback_type)
        return {"status": "ok"}

    @app.post("/api/news/search")
    async def search(payload: SearchRequest) -> dict[str, Any]:
        time_range = parse_range(payload.time_range)
        results = await search_service.search(payload.query, payload.category_scope, payload.source_scope, time_range, payload.max_results)
        return {"items": results}

    @app.post("/api/news/deep-dive")
    async def deep_dive(payload: DeepDiveRequest) -> dict[str, Any]:
        return await services["deep_dive"].run(
            payload.query,
            category_scope=payload.category_scope,
            source_scope=payload.source_scope,
            rounds=payload.rounds,
            breadth=payload.breadth,
        )

    @app.post("/api/news/search/ingest")
    async def native_search_ingest(payload: NativeSearchIngestRequest) -> dict[str, Any]:
        return await services["native_ingestion"].ingest(
            query=payload.query,
            category_scope=payload.category_scope,
            source_scope=payload.source_scope,
            max_results=payload.max_results,
            fetch_articles=payload.fetch_articles,
            follow_depth=payload.follow_depth,
            follow_limit_per_article=payload.follow_limit_per_article,
        )

    @app.post("/api/topics/view")
    async def topic_view(payload: TopicViewRequest) -> dict[str, Any]:
        return await services["topic_views"].build(
            topic=payload.topic,
            category_scope=payload.category_scope,
            source_scope=payload.source_scope,
            max_articles=payload.max_articles,
        )

    @app.get("/api/topics")
    async def list_topics(user_id: str = "default", topic_type: str | None = None, limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
        return {"items": services["topic_agent"].list_topics(user_id=user_id, topic_type=topic_type, limit=limit)}

    @app.post("/api/topics")
    async def create_topic(payload: TopicCreateRequest) -> dict[str, Any]:
        try:
            if payload.text:
                return await services["topic_agent"].create_topic_from_text(
                    user_id=payload.user_id,
                    text=payload.text,
                    category_scope=payload.category_scope,
                    schedule=payload.schedule,
                    refresh_now=payload.refresh_now,
                )
            return await services["topic_agent"].create_topic(
                user_id=payload.user_id,
                title=payload.title or "",
                category_scope=payload.category_scope,
                schedule=payload.schedule,
                topic_type=payload.topic_type,
                refresh_now=payload.refresh_now,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/news/search/backend")
    async def search_backend() -> dict[str, Any]:
        return {
            "configured_backend": settings.search_backend,
            "local_backend": "sqlite_fts",
            "primary_recall_backend": "elasticsearch" if search_index.configured else "sqlite_fts",
            "external_provider": settings.external_search_provider,
            "external_configured": bool(settings.bing_search_key) if settings.external_search_provider == "bing" else False,
            "elasticsearch": await search_index.health(),
            "crawl_url_store": {
                "backend": settings.crawl_url_backend,
                "mysql_configured": bool(settings.crawl_database_url),
                "mysql_ready": url_store.ready,
            },
        }

    @app.get("/api/events")
    async def list_events(category: str | None = None, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        clusters = events.discover(category=category, limit=limit)
        return {"items": clusters}

    @app.post("/api/chat")
    async def chat(payload: ChatRequest) -> Any:
        return await services["chat"].chat(payload.conversation_id, payload.message, payload.topic, payload.category_scope, payload.use_llm, user_id=payload.user_id)

    @app.post("/api/chat/stream")
    async def chat_stream(payload: ChatRequest) -> StreamingResponse:
        async def event_stream():
            async for event in services["chat"].chat_events(payload.conversation_id, payload.message, payload.topic, payload.category_scope, payload.use_llm, user_id=payload.user_id):
                event_type = event.get("type", "message")
                data = json.dumps(event, ensure_ascii=False, default=str)
                yield f"event: {event_type}\ndata: {data}\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    @app.post("/api/reports")
    async def reports(payload: ReportRequest) -> Any:
        return await services["reports"].generate(payload.user_id, payload.topic, payload.category_scope, payload.time_range, payload.report_type)

    @app.post("/api/tasks")
    async def create_task(payload: TaskRequest) -> dict[str, Any]:
        try:
            return services["tasks"].create_task(payload.model_dump())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/tasks")
    async def list_tasks(user_id: str = "default", limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
        return {"items": services["tasks"].list_tasks(user_id=user_id, limit=limit)}

    @app.post("/api/tasks/due/run")
    async def run_due_tasks(payload: DueTasksRequest) -> dict[str, Any]:
        return await services["tasks"].run_due_tasks(user_id=payload.user_id, limit=payload.limit)

    @app.post("/api/tasks/{task_id}/run")
    async def run_task(task_id: str) -> dict[str, Any]:
        try:
            return await services["tasks"].run_task(task_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/notifications")
    async def notifications(user_id: str = "default", unread_only: bool = False, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
        return {"items": store.list_notifications(user_id=user_id, unread_only=unread_only, limit=limit)}

    @app.post("/api/notifications/{notification_id}/read")
    async def read_notification(notification_id: str, payload: NotificationReadRequest) -> dict[str, Any]:
        item = store.mark_notification_read(notification_id, payload.user_id)
        if not item:
            raise HTTPException(status_code=404, detail="notification not found")
        return {"item": item}

    @app.post("/api/crawl/{category}")
    async def crawl(category: str) -> dict[str, Any]:
        try:
            return await services["crawl"].crawl_category(category)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
