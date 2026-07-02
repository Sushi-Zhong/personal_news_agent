from __future__ import annotations

from typing import Any

from personal_news_agent.config import Settings
from personal_news_agent.services.auth import AuthService
from personal_news_agent.services.chat import NewsChatService
from personal_news_agent.services.crawl import CrawlScheduler
from personal_news_agent.services.deep_dive import DeepDiveService
from personal_news_agent.services.events import EventDiscoveryService
from personal_news_agent.services.model_config import public_model_options
from personal_news_agent.services.native_ingestion import NativeSearchIngestionService
from personal_news_agent.services.onboarding import OnboardingService
from personal_news_agent.services.personalization import PersonalizationService
from personal_news_agent.services.reports import ReportGenerationService
from personal_news_agent.services.search import UnifiedSearchService, external_provider_from_settings
from personal_news_agent.services.search_index import ArticleSearchIndex, ElasticsearchArticleIndex
from personal_news_agent.services.source_registry import SourceRegistryService
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.tasks import ScheduledTaskService
from personal_news_agent.services.topic_agent import TopicAgentService
from personal_news_agent.services.topic_views import TopicViewService
from personal_news_agent.services.url_store import CrawlUrlStore, MySQLCrawlUrlStore


def build_services(settings: Settings) -> dict[str, Any]:
    registry = SourceRegistryService(settings.sources_path)
    store = NewsStore(settings.sqlite_path)
    search_index = ElasticsearchArticleIndex.from_settings(settings) or ArticleSearchIndex()
    url_store = MySQLCrawlUrlStore.from_settings(settings) or CrawlUrlStore()
    search_service = UnifiedSearchService(store, registry, external_provider_from_settings(settings), search_index)
    events = EventDiscoveryService(store)
    native_ingestion = NativeSearchIngestionService(registry, store, url_store, search_index)
    topic_views = TopicViewService(store, search_service)
    deep_dive = DeepDiveService(search_service)
    reports = ReportGenerationService(store, search_service)
    tasks = ScheduledTaskService(store, reports)
    topic_agent = TopicAgentService(store, tasks, topic_views=topic_views, native_ingestion=native_ingestion)
    chat = NewsChatService(
        store,
        search_service,
        native_ingestion=native_ingestion,
        deep_dive=deep_dive,
        topic_views=topic_views,
        topic_agent=topic_agent,
    )

    return {
        "registry": registry,
        "store": store,
        "url_store": url_store,
        "search_index": search_index,
        "search": search_service,
        "native_ingestion": native_ingestion,
        "topic_views": topic_views,
        "deep_dive": deep_dive,
        "events": events,
        "auth": AuthService(store, settings),
        "onboarding": OnboardingService(store, settings),
        "feed": PersonalizationService(store, registry),
        "model_options": public_model_options,
        "reports": reports,
        "tasks": tasks,
        "topic_agent": topic_agent,
        "chat": chat,
        "crawl": CrawlScheduler(registry, store, url_store, search_index),
    }
