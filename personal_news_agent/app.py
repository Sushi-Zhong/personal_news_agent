from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from personal_news_agent.api.routes import register_routes
from personal_news_agent.config import settings
from personal_news_agent.services.factory import build_services
from personal_news_agent.services.source_registry import SourceRegistryError


def create_app() -> FastAPI:
    services = build_services(settings)
    app = FastAPI(title=settings.app_name)
    app.state.services = services

    static_dir = Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    register_routes(app, services, static_dir, settings)

    @app.on_event("startup")
    async def startup() -> None:
        registry = services["registry"]
        store = services["store"]
        url_store = services["url_store"]
        search_index = services["search_index"]
        events = services["events"]

        try:
            registry.load()
        except SourceRegistryError:
            raise
        store.init()
        store.upsert_sources(registry.all_sources())
        try:
            url_store.init()
            url_store.sync_sources(registry.all_sources())
        except Exception as exc:
            store.log("crawl_url_store_init", "error", "mysql", {"error": str(exc)})
        try:
            await search_index.ensure_index()
        except Exception as exc:
            store.log("search_index_init", "error", "elasticsearch", {"error": str(exc)})
        if settings.seed_demo_data:
            store.seed_demo_articles()
        services["topic_agent"].seed_system_topics()
        events.discover(limit=20)

    return app


app = create_app()
