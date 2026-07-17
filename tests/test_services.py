from pathlib import Path
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from personal_news_agent.config import Settings
from personal_news_agent.core.models import RawArticle, RawArticleLink, RawSearchResult, SearchResult, TimeRange
from personal_news_agent.services.chat import NewsChatService, _filter_by_time, _rank_for_chat
from personal_news_agent.services.crawl import CrawlScheduler
from personal_news_agent.services.deep_dive import DeepDiveService
from personal_news_agent.services.events import EventDiscoveryService
from personal_news_agent.services.article_fetch import _parse_published_datetime, _unwrap_search_link
from personal_news_agent.services.chat_understanding import (
    categories_for_message,
    is_contextual_followup,
    query_from_message,
)
from personal_news_agent.services.native_ingestion import NativeSearchIngestionService
from personal_news_agent.services.personalization import PersonalizationService
from personal_news_agent.services.reports import ReportGenerationService
from personal_news_agent.services.search import (
    ExternalSearchProvider,
    TavilySearchProvider,
    UnifiedSearchService,
    _relevance_terms,
    external_provider_from_settings,
    search_result_matches_subject,
    search_result_matches_terms,
)
from personal_news_agent.services.source_adapter import ListPageAdapter
from personal_news_agent.services.source_registry import SourceRegistryService
from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.tasks import ScheduledTaskService
from personal_news_agent.services.topic_agent import TopicAgentService
from personal_news_agent.services.topic_views import TopicViewService


@pytest.fixture()
def services(tmp_path):
    registry = SourceRegistryService(Path("sources.yaml"))
    registry.load()
    store = NewsStore(tmp_path / "news.db")
    store.init()
    store.upsert_sources(registry.all_sources())
    store.seed_demo_articles()
    search = UnifiedSearchService(store, registry)
    return registry, store, search


def test_search_works_without_external_provider(services):
    _, _, search = services
    results = asyncio.run(search.search("AI Agent", ["tech"], None, None, 10))
    assert results
    assert all(item.category == "tech" for item in results)


def test_tavily_provider_maps_search_results():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer tvly-test"
        payload = json.loads(request.content)
        assert payload["query"] == "上海今天重要新闻"
        assert payload["search_depth"] == "basic"
        assert payload["max_results"] == 3
        assert payload["include_domains"] == ["shio.gov.cn"]
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": "https://www.shio.gov.cn/news/1",
                        "title": "上海今日新闻",
                        "content": "上海发布最新消息。",
                        "score": 0.91,
                    }
                ]
            },
        )

    provider = TavilySearchProvider("tvly-test", transport=httpx.MockTransport(handler))
    results = asyncio.run(provider.search("上海今天重要新闻", ["shio.gov.cn"], 3))
    assert len(results) == 1
    assert results[0].source_id == "shio.gov.cn"
    assert results[0].title == "上海今日新闻"
    assert results[0].snippet == "上海发布最新消息。"


def test_external_provider_factory_selects_tavily():
    configured = Settings(external_search_provider="tavily", tavily_api_key="tvly-test")
    missing_key = Settings(external_search_provider="tavily", tavily_api_key=None)
    assert isinstance(external_provider_from_settings(configured), TavilySearchProvider)
    assert not external_provider_from_settings(missing_key).configured


def test_external_search_requires_explicit_permission(services):
    registry, store, _ = services
    provider = FakeExternalProvider()
    search = UnifiedSearchService(store, registry, external_provider=provider)
    asyncio.run(search.search("上海实时天气", ["tech"], None, None, 5))
    assert provider.calls == 0
    results = asyncio.run(search.search("上海实时天气", ["tech"], None, None, 5, include_remote=True))
    assert provider.calls == 1
    assert any(item.origin == "external" for item in results)


def test_external_semantic_results_are_not_dropped_by_exact_sentence_filter(services):
    registry, store, _ = services
    search = UnifiedSearchService(store, registry, external_provider=SemanticExternalProvider())
    query = "机车相关一些信息。提供一些开发商"
    assert _relevance_terms(query)[:2] == ["机车", "开发商"]
    results = asyncio.run(search.search_external(query, None, None, 5))
    assert [item.title for item in results] == ["全球主要摩托车制造商与品牌"]


def test_expansion_evidence_must_match_original_subject():
    query = "机车相关一些信息。提供一些开发商"
    unrelated = SearchResult(
        source_id="old-news",
        title="社会新闻汇总",
        url="https://example.com/old",
        summary="内容提到了房地产开发商和政策调整。",
        category="politics",
        origin="local",
    )
    related = SearchResult(
        source_id="motorcycle-news",
        title="当前机车品牌及主要制造商介绍",
        url="https://example.com/motorcycle",
        summary="介绍机车品牌、车型和制造企业。",
        category="auto",
        origin="external",
    )
    assert not search_result_matches_subject(query, unrelated)
    assert search_result_matches_subject(query, related)


def test_long_chinese_query_keeps_full_subject_ahead_of_short_prefixes():
    query = "背景事件对目标对象的影响 近期变化"
    terms = _relevance_terms(query)
    assert terms[0] == "背景事件对目标对象的影响"
    assert terms.index("背景") > terms.index("背景事件对目标对象的影响")


def test_planner_terms_filter_broad_background_results():
    unrelated = SearchResult(
        source_id="background",
        title="背景事件最新进展",
        url="https://example.com/background",
        summary="介绍背景事件本身的动态。",
        category="politics",
        origin="external",
    )
    related = SearchResult(
        source_id="subject",
        title="目标对象出现新的变化",
        url="https://example.com/subject",
        summary="分析目标对象受到的影响。",
        category="economy",
        origin="external",
    )
    required_terms = ["目标对象", "对象变化"]
    assert not search_result_matches_terms(required_terms, unrelated)
    assert search_result_matches_terms(required_terms, related)


def test_search_query_plan_is_generated_from_user_input(services):
    _, store, search = services
    llm = FakeSearchPlannerLLM()
    chat = NewsChatService(store, search, llm_client=llm)
    plan = asyncio.run(
        chat._plan_search_query(
            "背景事件对目标对象有什么影响？",
            None,
            "背景事件对目标对象 影响",
            TimeRange(days=14),
            True,
        )
    )
    assert plan.source == "llm"
    assert plan.query == "目标对象 影响 背景事件 近期变化"
    assert plan.primary_subject == "目标对象受到的影响"
    assert plan.required_terms == ["目标对象", "对象变化"]
    assert llm.calls == 1


def test_search_query_plan_falls_back_without_web_permission(services):
    _, store, search = services
    llm = FakeSearchPlannerLLM()
    chat = NewsChatService(store, search, llm_client=llm)
    plan = asyncio.run(chat._plan_search_query("原始问题", None, "完整原始查询", None, False))
    assert plan.query == "完整原始查询"
    assert plan.source == "fallback"
    assert llm.calls == 0


def test_related_search_uses_local_agent_queries_and_saves_turn(services):
    _, store, _ = services
    search = FakeRelatedSearchService()
    local_agent = FakeRelatedLocalAgent()
    chat = NewsChatService(store, search, local_agent=local_agent)
    response = asyncio.run(
        chat.related_search(
            "related_conv",
            "AI Agent",
            category_scope=["tech"],
            user_id="default",
            max_queries=2,
        )
    )
    assert response.context_relation == "related_search"
    assert [item["query"] for item in response.expanded_queries] == ["AI Agent 最新进展", "AI Agent 产业影响"]
    assert response.mind_map
    assert response.mind_map["topic"] == "AI Agent"
    assert [branch["title"] for branch in response.mind_map["branches"]] == ["AI Agent 最新进展", "AI Agent 产业影响"]
    assert response.mind_map["branches"][0]["relation_label"] == "最新进展"
    assert response.mind_map["branches"][0]["edge_reason"] == "查看近期变化"
    assert response.mind_map["branches"][0]["points"][0]["title"] == "AI Agent 最新进展 报道"
    assert response.mind_map["branches"][0]["points"][0]["connection_reason"] == "检索命中「最新进展」方向"
    assert "相关思维导图" in response.answer
    assert search.queries == ["AI Agent 最新进展", "AI Agent 产业影响"]
    assert response.recommendations
    turns = store.list_turns("related_conv", "default")
    assert turns[-1]["response"]["context_relation"] == "related_search"


def test_chat_source_ingestion_requires_explicit_permission(services):
    _, store, search = services
    ingestion = FakeNativeIngestion()
    chat = NewsChatService(store, search, llm_client=FakeDisabledLLM(), native_ingestion=ingestion)
    disabled = asyncio.run(chat._research_chat("offline", "上海天气", allow_web_search=False))
    assert ingestion.calls == 0
    assert any(item["stage"] == "源搜索入库" and item["status"] == "skipped" for item in disabled.research_trace)

    asyncio.run(chat._research_chat("online", "上海天气", allow_web_search=True))
    assert ingestion.calls == 1


def test_time_filter_keeps_current_external_results_without_published_date():
    old_local = SearchResult(
        source_id="local",
        title="上海旧闻",
        url="https://example.com/old",
        summary="上海天气",
        category="tech",
        published_at=datetime.now(timezone.utc) - timedelta(days=10),
        origin="local",
    )
    live_external = SearchResult(
        source_id="shio.gov.cn",
        title="上海实时消息",
        url="https://www.shio.gov.cn/live",
        summary="上海今天的重要消息",
        category="tech",
        origin="external",
    )
    filtered = _filter_by_time([old_local, live_external], TimeRange(days=1))
    ranked = _rank_for_chat(filtered, "上海今天有什么重要新闻")
    assert [item.url for item in ranked] == [live_external.url]


def test_chat_understanding_overrides_stale_sports_scope_for_game_topic():
    assert categories_for_message("我想知道游戏资讯的最近消息", "当前机车品牌", ["sports"]) == ["game"]
    assert query_from_message("我想知道游戏资讯的最近消息", "当前机车品牌") == "资讯 消息"


def test_chat_understanding_keeps_topic_for_contextual_followup():
    query = query_from_message("还有别的什么机车比赛？我也想知道他们这方面的消息", "当前机车品牌")
    assert query.startswith("当前机车品牌")
    assert categories_for_message("还有别的什么机车比赛？", "当前机车品牌", ["sports"]) == ["sports"]


def test_chat_understanding_preserves_comparison_intent_with_current_topic():
    message = "给我一些别的机车公司的信息，这些公司要和当前机车品牌很像"
    query = query_from_message(message, "当前机车品牌")
    assert query != "当前机车品牌"
    assert "机车公司" in query
    assert "当前机车品牌" in query
    assert "很像" in query
    assert categories_for_message(message, "当前机车品牌", ["sports"]) == ["auto"]


def test_conversation_context_uses_last_saved_topic(services):
    _, store, search = services
    store.save_turn("context_memory", "介绍主题甲", "主题甲回答", [], None, user_id="user_context", topic="主题甲")
    chat = NewsChatService(store, search)
    topic, categories = chat._resolve_conversation_context(
        "context_memory",
        "继续比较刚刚问的内容",
        "默认主题",
        None,
        "user_context",
    )
    assert topic == "主题甲"
    assert categories is None


def test_conversation_context_uses_recent_topic_across_conversations(services):
    _, store, search = services
    store.save_turn(
        "previous_context_memory",
        "介绍主题甲",
        "主题甲回答",
        [],
        None,
        user_id="cross_context_user",
        topic="主题甲",
        category_scope=["auto"],
    )
    chat = NewsChatService(store, search)

    topic, categories = chat._resolve_conversation_context(
        "new_context_memory",
        "继续说说刚才那个",
        None,
        None,
        "cross_context_user",
    )

    assert topic == "主题甲"
    assert categories == ["auto"]
    memory = chat._conversation_memory("new_context_memory", "cross_context_user")
    assert any(item["conversation_id"] == "previous_context_memory" for item in memory)


def test_chat_does_not_auto_create_attention_topic_for_normal_question(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    topic_agent = TopicAgentService(store, tasks)
    chat = NewsChatService(store, search, topic_agent=topic_agent)

    response = asyncio.run(
        chat.chat(
            "normal_question_topic",
            "张雪机车有什么值得关注的新变化？",
            topic="张雪机车",
            category_scope=["sports"],
            user_id="topic_guard_user",
        )
    )

    assert response.context_relation != "topic_agent_created"
    topics = [item for item in store.list_topics("topic_guard_user") if item["topic_type"] == "user"]
    assert topics == []


def test_topic_agent_creates_from_next_message_after_create_command(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    topic_agent = TopicAgentService(store, tasks)
    chat = NewsChatService(store, search, topic_agent=topic_agent)

    skipped = asyncio.run(
        topic_agent.maybe_create_topic_from_chat(
            "explicit_topic_user",
            "帮我关注张雪机车有什么值得关注的新变化",
        )
    )
    assert skipped is None

    pending = asyncio.run(
        chat.chat(
            "explicit_topic_conv",
            "创建一个新的长期专题任务",
            user_id="explicit_topic_user",
        )
    )
    assert pending.context_relation == "topic_create_pending"

    created = asyncio.run(
        chat.chat(
            "explicit_topic_conv",
            "张雪机车有什么值得关注的新变化",
            user_id="explicit_topic_user",
        )
    )
    assert created.context_relation == "topic_agent_created"
    assert created.focus_object.text == "张雪机车"


def test_topic_agent_creates_from_inline_create_command(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    topic_agent = TopicAgentService(store, tasks)
    chat = NewsChatService(store, search, topic_agent=topic_agent)

    created = asyncio.run(
        chat.chat(
            "inline_topic_conv",
            "创建一个新的长期专题任务 无锡水蜜桃",
            user_id="inline_topic_user",
        )
    )

    assert created.context_relation == "topic_agent_created"
    assert created.focus_object.text == "无锡水蜜桃"
    topics = [item for item in store.list_topics("inline_topic_user") if item["topic_type"] == "user"]
    assert any(item["title"] == "无锡水蜜桃" for item in topics)


def test_topics_are_scoped_by_conversation(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    topic_agent = TopicAgentService(store, tasks)

    asyncio.run(
        topic_agent.create_topic(
            user_id="scoped_topic_user",
            title="无锡水蜜桃",
            conversation_id="topic_conv_a",
            refresh_now=False,
        )
    )
    asyncio.run(
        topic_agent.create_topic(
            user_id="scoped_topic_user",
            title="贵州茅台",
            conversation_id="topic_conv_b",
            refresh_now=False,
        )
    )

    conv_a = [
        item["title"]
        for item in topic_agent.list_topics(
            user_id="scoped_topic_user",
            conversation_id="topic_conv_a",
        )
        if item["topic_type"] == "user"
    ]
    conv_b = [
        item["title"]
        for item in topic_agent.list_topics(
            user_id="scoped_topic_user",
            conversation_id="topic_conv_b",
        )
        if item["topic_type"] == "user"
    ]

    assert conv_a == ["无锡水蜜桃"]
    assert conv_b == ["贵州茅台"]


def test_conversation_turns_round_trip_full_response(services):
    _, store, _ = services
    store.save_turn(
        "conv_history",
        "游戏资讯最近有什么消息？",
        "这里是回答",
        [],
        {"type": "topic", "text": "游戏资讯 消息"},
        user_id="user_history",
        response={"conversation_id": "conv_history", "answer": "这里是回答", "context_relation": "test"},
        topic="游戏资讯 消息",
        category_scope=["game"],
    )
    turns = store.list_turns("conv_history", "user_history")
    assert turns[0]["response"]["answer"] == "这里是回答"
    assert turns[0]["topic"] == "游戏资讯 消息"
    assert turns[0]["category_scope"] == ["game"]


def test_conversation_history_is_grouped_by_user(services):
    _, store, _ = services
    store.save_turn("conv_one", "第一段对话", "回答一", [], None, user_id="history_user", topic="主题一")
    store.save_turn("conv_one", "继续追问", "回答二", [], None, user_id="history_user", topic="主题一")
    store.save_turn("conv_two", "第二段对话", "回答三", [], None, user_id="history_user", topic="主题二")
    store.save_turn("other_conv", "其他用户", "其他回答", [], None, user_id="other_user")

    items = store.list_conversations("history_user")
    by_id = {item["conversation_id"]: item for item in items}
    assert set(by_id) == {"conv_one", "conv_two"}
    assert by_id["conv_one"]["first_message"] == "第一段对话"
    assert by_id["conv_one"]["last_message"] == "继续追问"
    assert by_id["conv_one"]["turn_count"] == 2
    assert by_id["conv_one"]["topic"] == "主题一"


def test_search_prefers_elasticsearch_index(services):
    registry, store, _ = services
    search = UnifiedSearchService(store, registry, search_index=FakeArticleIndex())
    results = asyncio.run(search.search("国际局势 农作物", ["politics"], None, None, 10))
    assert results
    assert results[0].origin == "elasticsearch"
    assert results[0].source_id == "people_politics"


def test_native_source_search_encodes_query(services):
    registry, _, _ = services
    source = registry.get_source("hupu")
    fetcher = FakeLinkFetcher()
    results = asyncio.run(ListPageAdapter(source, fetcher=fetcher).search("机车赛事", limit=2))
    assert results
    assert "%E6%9C%BA%E8%BD%A6%E8%B5%9B%E4%BA%8B" in fetcher.urls[0]
    assert "{query" not in fetcher.urls[0]


def test_search_redirect_link_unwraps_targetpage():
    wrapped = "https://search.cctv.com/link_p.php?targetpage=https%3A%2F%2Fsports.cctv.com%2F2026%2F06%2F01%2FARTITest.shtml&point=web"
    assert _unwrap_search_link(wrapped) == "https://sports.cctv.com/2026/06/01/ARTITest.shtml"


def test_parse_published_datetime_assumes_china_timezone_for_naive_time():
    parsed = _parse_published_datetime("2026年05月18日 10:30")
    assert parsed.isoformat() == "2026-05-18T02:30:00+00:00"


def test_native_search_ingestion_fetches_and_indexes_articles(services):
    registry, store, _ = services
    fake_fetcher = FakeLinkFetcher()
    fake_index = FakeWriteIndex()
    service = NativeSearchIngestionService(
        registry,
        store,
        search_index=fake_index,
        adapter_factory=lambda source: ListPageAdapter(source, fetcher=fake_fetcher),
    )

    payload = asyncio.run(
        service.ingest(
            query="机车赛事",
            category_scope=["sports"],
            source_scope=["hupu"],
            max_results=2,
            fetch_articles=1,
        )
    )

    assert payload["discovered_count"] == 1
    assert payload["fetched_count"] == 1
    assert payload["indexed_count"] == 1
    assert fake_index.indexed[0]["title"] == "机车赛事更新"
    saved = store.search_articles("机车赛事", ["sports"], limit=5)
    assert saved and saved[0]["source_id"] == "hupu"


def test_deep_dive_generates_expansion_queries_and_evidence(services):
    registry, store, _ = services
    search = UnifiedSearchService(store, registry)
    payload = asyncio.run(DeepDiveService(search).run("国际局势 农作物", ["politics"], None, rounds=1, breadth=2))
    assert payload["expanded_queries"]
    assert payload["evidence"]
    assert payload["strategy"]["llm_planner"].startswith("预留")


def test_event_discovery_generates_required_fields(services):
    _, store, _ = services
    clusters = EventDiscoveryService(store).discover(category="auto")
    assert clusters
    cluster = clusters[0]
    assert cluster.title
    assert cluster.category == "auto"
    assert cluster.article_count >= 1
    assert cluster.source_count >= 1
    assert cluster.hot_score > 0


def test_personalized_feed_changes_with_profile(services):
    registry, store, _ = services
    feed = PersonalizationService(store, registry)
    default_first = feed.feed("default", limit=1)[0]
    store.save_profile(
        {
            "user_id": "sports_user",
            "interests": ["球队"],
            "negative_interests": [],
            "preferred_categories": ["sports"],
            "preferred_sources": [],
            "output_style": "concise",
        }
    )
    sports_first = feed.feed("sports_user", limit=1)[0]
    assert default_first.category != sports_first.category
    assert sports_first.category == "sports"
    assert "sports" in sports_first.matched_profile_terms
    assert sports_first.source_tags


def test_personalized_feed_covers_multiple_preferred_categories(services):
    registry, store, _ = services
    store.save_profile(
        {
            "user_id": "politics_sports_user",
            "self_description": "关心时政和体育，重点看 NBA 和粮食安全。",
            "interests": ["NBA", "粮食"],
            "negative_interests": [],
            "preferred_categories": ["politics", "sports"],
            "preferred_sources": [],
            "output_style": "简洁分析型",
        }
    )

    items = PersonalizationService(store, registry).feed("politics_sports_user", limit=6)
    categories = [item.category for item in items]
    assert "politics" in categories
    assert "sports" in categories
    assert any("politics" in item.source_tags for item in items if item.category == "politics")
    assert any("sports" in item.matched_profile_terms for item in items)


def test_source_due_plan_uses_crawl_metadata(services):
    registry, store, _ = services
    scheduler = CrawlScheduler(registry, store)
    plan = scheduler.due_plan(category="tech", limit=5)
    assert plan["sections"]
    assert plan["due_count"] >= 1
    first = plan["sections"][0]
    assert first["category"] == "tech"
    assert first["source_tags"]
    store.mark_section_crawled(first["source_id"], first["section_key"])
    updated = scheduler.due_plan(category="tech", limit=5)
    same = [item for item in updated["sections"] if item["source_id"] == first["source_id"] and item["section_key"] == first["section_key"]]
    assert same and same[0]["due"] is False


def test_chat_resolves_second_article_followup(services):
    _, store, search = services
    chat = NewsChatService(store, search)
    first = asyncio.run(chat.chat("conv_test", "今天游戏圈有什么新闻？"))
    assert len(first.recommendations) >= 2

    second = asyncio.run(chat.chat("conv_test", "第二条展开说说。"))
    assert second.context_relation == "follow_up"
    assert second.focus_object is not None
    assert second.focus_object.type == "article"
    assert second.focus_object.ordinal == 2
    assert "previous_recommendation_list" in second.required_context_items


def test_report_contains_timeline_and_required_sections(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    report = asyncio.run(reports.generate("default", "新能源汽车价格战", ["auto", "economy"]))

    assert report.timeline
    assert "一、结论摘要" in report.sections
    assert "三、关键时间线" in report.sections
    assert "八、来源列表与不确定性说明" in report.sections
    with store.connect() as conn:
        operations = [row["operation"] for row in conn.execute("SELECT operation FROM operation_logs").fetchall()]
    assert "news_search" in operations
    assert "report_generation" in operations


def test_topic_view_builds_event_line_and_relation_graph(services):
    _, store, search = services
    payload = asyncio.run(TopicViewService(store, search).build("新能源汽车价格战", ["auto", "economy"], None, max_articles=8))

    assert payload["topic"]["title"] == "新能源汽车价格战"
    assert payload["event_line"]["items"]
    assert payload["relation_graph"]["nodes"]
    assert payload["relation_graph"]["edges"]
    assert payload["relation_graph"]["nodes"][0]["type"] == "topic"


def test_scheduled_task_runs_and_generates_report(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    task = tasks.create_task(
        {
            "user_id": "default",
            "task_type": "daily_digest",
            "schedule": "0 21 * * *",
            "category_scope": ["tech", "game", "auto"],
            "topics": ["AI", "任天堂", "新能源汽车"],
            "output_style": "简洁分析型",
        }
    )
    assert task["next_run_at"]
    result = asyncio.run(tasks.run_task(task["id"]))
    assert result["status"] == "ok"
    assert result["report_id"].startswith("rpt_")
    assert result["notification"]["target_id"] == result["report_id"]
    notifications = store.list_notifications("default")
    assert notifications
    assert notifications[0]["payload"]["task_id"] == task["id"]


def test_due_tasks_create_notifications(services):
    _, store, search = services
    reports = ReportGenerationService(store, search)
    tasks = ScheduledTaskService(store, reports)
    task = tasks.create_task(
        {
            "user_id": "due_user",
            "task_type": "topic_tracking",
            "schedule": "*/20 * * * *",
            "category_scope": ["sports"],
            "topics": ["机车赛事"],
            "delivery_channel": "browser",
        }
    )
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with store.connect() as conn:
        conn.execute("UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?", (past, task["id"]))

    result = asyncio.run(tasks.run_due_tasks("due_user"))
    assert result["ran_count"] == 1
    assert result["notifications"][0]["delivery_channel"] == "browser"
    assert store.get_task(task["id"])["next_run_at"] > past


class FakeArticleIndex:
    configured = True

    async def search(self, query, category_scope=None, source_scope=None, limit=20):
        return [
            {
                "id": "art_es_seed",
                "source_id": "people_politics",
                "title": "国际局势影响农作物出口",
                "url": "https://example.local/es",
                "summary": "粮食安全和农作物价格受到关注。",
                "category": "politics",
                "published_at": None,
            }
        ][:limit]


class FakeExternalProvider(ExternalSearchProvider):
    configured = True

    def __init__(self):
        self.calls = 0

    async def search(self, query, domains, limit):
        self.calls += 1
        return [
            RawSearchResult(
                source_id="weather.example",
                title="上海实时天气",
                url="https://weather.example/shanghai",
                snippet="上海实时天气更新。",
            )
        ][:limit]


class SemanticExternalProvider(ExternalSearchProvider):
    configured = True

    async def search(self, query, domains, limit):
        return [
            RawSearchResult(
                source_id="motorcycle.example",
                title="全球主要摩托车制造商与品牌",
                url="https://motorcycle.example/manufacturers",
                snippet="介绍本田、雅马哈、川崎、宝马等摩托车制造商。",
            )
        ][:limit]


class FakeNativeIngestion:
    def __init__(self):
        self.calls = 0

    async def ingest(self, **kwargs):
        self.calls += 1
        return {
            "discovered_count": 0,
            "fetched_count": 0,
            "indexed_count": 0,
            "mysql_ready": False,
            "elasticsearch_configured": False,
        }


class FakeDisabledLLM:
    configured = False


class FakeSearchPlannerLLM:
    configured = True

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, model_key=None):
        self.calls += 1
        return (
            "```json\n"
            '{"query":"目标对象 影响 背景事件 近期变化",'
            '"primary_subject":"目标对象受到的影响",'
            '"required_terms":["目标对象","对象变化"],'
            '"keywords":["目标对象","影响","变化"]}'
            "\n```"
        )


class FakeRelatedMessage:
    content = (
        '{"queries":['
        '{"query":"AI Agent 最新进展","relation_type":"latest","reason":"查看近期变化"},'
        '{"query":"AI Agent 产业影响","relation_type":"impact","reason":"查看影响面"}'
        "]}"
    )


class FakeRelatedLocalAgent:
    def __init__(self):
        self.calls = 0

    async def chat(self, payload):
        self.calls += 1
        return type("FakeRelatedResponse", (), {"status": "ok", "message": FakeRelatedMessage()})()


class FakeRelatedSearchService:
    def __init__(self):
        self.queries = []

    async def search(self, query, category_scope, source_scope, time_range, max_results=20, include_remote=False):
        self.queries.append(query)
        return [
            SearchResult(
                source_id="test",
                title=f"{query} 报道",
                url=f"https://example.com/{len(self.queries)}",
                summary=f"{query} 的摘要",
                category=(category_scope or ["tech"])[0],
                published_at=datetime.now(timezone.utc),
                score=1.0,
                origin="local",
            )
        ]


class FakeLinkFetcher:
    def __init__(self):
        self.urls = []

    async def list_links(self, source_id, section_key, url, limit=30, allowed_domains=None):
        self.urls.append(url)
        return [
            RawArticleLink(
                source_id=source_id,
                section_key=section_key,
                title="机车赛事更新",
                url="https://bbs.hupu.com/639652293.html",
            )
        ][:limit]

    async def fetch_article(self, source_id, url):
        return RawArticle(
            source_id=source_id,
            url=url,
            title="机车赛事更新",
            summary="机车赛事继续受到关注。",
            content="机车赛事继续受到关注，车队成绩、商业合作和舆论讨论同步升温。",
        )


class FakeWriteIndex:
    configured = True

    def __init__(self):
        self.indexed = []

    async def ensure_index(self):
        return {"configured": True, "ready": True}

    async def index_article(self, article):
        self.indexed.append(article)

    async def search(self, query, category_scope=None, source_scope=None, limit=20):
        return []
