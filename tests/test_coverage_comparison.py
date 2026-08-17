from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from personal_news_agent.core.models import SearchResult
from personal_news_agent.services.cc_runtime import CCRuntimeResult
from personal_news_agent.services.coverage_comparison import CoverageComparisonService
from personal_news_agent.skills.arguments import CompareArguments
from personal_news_agent.skills.base import SkillContext
from personal_news_agent.skills.compare import CompareSkill
from personal_news_agent.skills.catalog import all_definitions
from personal_news_agent.skills.registry import build_default_registry


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _result(
    source: str,
    title: str,
    url: str,
    summary: str,
    *,
    published_at: datetime | None = NOW,
    origin: str = "local",
) -> SearchResult:
    return SearchResult(
        source_id=source,
        title=title,
        url=url,
        summary=summary,
        category="tech",
        published_at=published_at,
        score=1.0,
        origin=origin,
    )


class _Store:
    def log(self, *args, **kwargs):
        return None


class _Search:
    def __init__(self, items):
        self.items = items
        self.calls = []

    async def search(self, query, category_scope, source_scope, time_range, max_results=20, include_remote=False):
        self.calls.append({"query": query, "source_scope": source_scope, "include_remote": include_remote})
        return list(self.items)


class _SequentialSearch:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def search(self, query, category_scope, source_scope, time_range, max_results=20, include_remote=False):
        self.calls.append(
            {
                "query": query,
                "category_scope": category_scope,
                "source_scope": source_scope,
                "include_remote": include_remote,
            }
        )
        return list(self.responses[len(self.calls) - 1])


class _Agent:
    configured = True

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def run(self, **kwargs):
        self.calls.append(kwargs)
        return CCRuntimeResult(
            answer=self.payload if isinstance(self.payload, str) else json.dumps(self.payload, ensure_ascii=False),
            results=[],
            queries=[],
            trace=[],
        )


def _coverage_items():
    syndicated = "通讯社消息：公司宣布 Agent 产品将于9月开放测试，首批覆盖100名用户，并公布申请方式。"
    return [
        _result("official", "公司公布 Agent 测试计划", "https://official.example/launch", "公司宣布 Agent 产品将在9月开放测试，首批邀请100名用户。"),
        _result("portal-a", "Agent 产品9月开放测试", "https://portal-a.example/a", syndicated),
        _result("portal-b", "Agent 产品正式来了", "https://portal-b.example/b", syndicated),
        _result("analysis", "激进扩张引担忧", "https://analysis.example/opinion", "报道确认产品9月开放测试、首批100名用户，并评论该策略较为激进。"),
    ]


def test_compare_merges_syndicated_same_copy_into_one_source_group() -> None:
    service = CoverageComparisonService(_Store(), _Search([]))

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    groups = outcome.data.source_groups
    syndicated_group = next(group for group in groups if {2, 3} <= set(group["evidence_indices"]))
    assert syndicated_group["reason"] == "syndicated_same_copy"
    assert len(groups) == 3
    assert outcome.status == "degraded"


def test_compare_deduplicates_tracking_variants_of_the_same_url() -> None:
    items = [
        _result("wire", "原稿", "https://example.com/story?utm_source=a", "一篇足够长的新闻正文，用于验证完全相同 URL 的追踪参数变体不会重复计数。"),
        _result("wire", "原稿副本", "https://example.com/story?utm_campaign=b", "一篇足够长的新闻正文，用于验证完全相同 URL 的追踪参数变体不会重复计数。"),
    ]
    service = CoverageComparisonService(_Store(), _Search([]))

    outcome = asyncio.run(service.analyze(topic="URL 去重", items=items))

    assert len(outcome.data.evidence) == 1
    assert outcome.data.source_groups[0]["evidence_indices"] == [1]


def test_compare_fallback_does_not_turn_headline_or_tone_into_fact_conflict() -> None:
    service = CoverageComparisonService(_Store(), _Search([]))

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    assert outcome.data.conflicts == []
    assert outcome.data.framing_differences
    assert any("标题" in item["text"] or "侧重" in item["text"] for item in outcome.data.framing_differences)


def test_compare_with_one_independent_group_is_insufficient() -> None:
    syndicated = _coverage_items()[1:3]
    service = CoverageComparisonService(_Store(), _Search([]))

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=syndicated))

    assert outcome.status == "success"
    assert outcome.data.comparison_status == "insufficient"
    assert len(outcome.data.source_groups) == 1


def test_compare_counts_multiple_articles_from_one_publisher_as_one_source_group() -> None:
    items = [
        _result("wire", "首篇报道", "https://wire.example/first", "公司公布产品计划，首篇报道介绍产品定位。"),
        _result("wire", "后续报道", "https://wire.example/follow-up", "公司回应市场问题，后续报道补充商业安排。"),
    ]
    service = CoverageComparisonService(_Store(), _Search([]))

    outcome = asyncio.run(service.analyze(topic="产品计划", items=items))

    assert outcome.data.comparison_status == "insufficient"
    assert len(outcome.data.source_groups) == 1
    assert outcome.data.source_groups[0]["evidence_indices"] == [1, 2]
    assert outcome.data.source_groups[0]["reason"] == "same_publisher"


def test_compare_agent_output_rejects_unknown_and_same_group_conflict_indices() -> None:
    agent = _Agent(
        {
            "comparison_status": "sufficient",
            "common_facts": [{"text": "9月开放测试", "evidence_indices": [1, 2, 999]}],
            "unique_claims": [{"text": "策略激进", "evidence_indices": [4]}],
            "conflicts": [
                {"text": "同稿伪冲突", "evidence_indices": [2, 3]},
                {"text": "真实冲突", "evidence_indices": [1, 4]},
            ],
            "framing_differences": [{"text": "标题侧重不同", "evidence_indices": [1, 4]}],
            "missing_questions": ["测试资格如何分配"],
        }
    )
    service = CoverageComparisonService(_Store(), _Search([]), cc_runtime=agent)
    conflicting_items = _coverage_items()
    conflicting_items[3] = _result(
        "analysis",
        "测试安排发生变化",
        "https://analysis.example/opinion",
        "报道确认产品改为10月开放测试、首批扩大至200名用户。",
    )

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=conflicting_items))

    assert outcome.status == "success"
    assert outcome.data.common_facts[0]["evidence_indices"] == [1, 2]
    assert [item["text"] for item in outcome.data.conflicts] == ["真实冲突"]
    assert all(index <= 4 for item in outcome.data.common_facts for index in item["evidence_indices"])
    assert agent.calls[0]["allow_web_search"] is False


def test_compare_drops_agent_conflict_based_only_on_wording_or_stance() -> None:
    agent = _Agent(
        {
            "comparison_status": "sufficient",
            "common_facts": [],
            "unique_claims": [],
            "conflicts": [{"text": "官方中性、分析稿激进", "evidence_indices": [1, 4]}],
            "framing_differences": [{"text": "标题和立场不同", "evidence_indices": [1, 4]}],
            "missing_questions": [],
        }
    )
    service = CoverageComparisonService(_Store(), _Search([]), cc_runtime=agent)

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    assert outcome.data.conflicts == []
    assert outcome.data.framing_differences[0]["text"] == "标题和立场不同"


def test_compare_keeps_source_specific_framing_with_its_own_evidence() -> None:
    agent = _Agent(
        {
            "comparison_status": "sufficient",
            "common_facts": [],
            "unique_claims": [],
            "conflicts": [],
            "framing_differences": [{"text": "官方稿侧重发布安排。", "evidence_indices": [1]}],
            "missing_questions": [],
        }
    )
    service = CoverageComparisonService(_Store(), _Search([]), cc_runtime=agent)

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    assert outcome.data.framing_differences == [
        {"text": "官方稿侧重发布安排。", "evidence_indices": [1]}
    ]


def test_compare_run_preserves_user_network_and_source_scope_for_retrieval() -> None:
    search = _Search(_coverage_items())
    service = CoverageComparisonService(_Store(), search)

    asyncio.run(
        service.run(
            topic="Agent 产品",
            category_scope=["tech"],
            source_scope=["official", "analysis"],
            include_remote=True,
        )
    )

    assert search.calls == [{"query": "Agent 产品", "source_scope": ["official", "analysis"], "include_remote": True}]


def test_compare_retries_across_categories_when_scoped_results_have_one_source() -> None:
    topic = 'GLM-5.3突然发布！唐杰的“sooooooon”这次兑现了'
    qq = _result(
        "qq_news",
        'GLM-5.3突然发布！唐杰的“sooooooon”这次兑现了',
        "https://new.qq.com/glm-5-3",
        "智谱发布 GLM-5.3，唐杰确认新模型已经上线。",
    )
    sina = _result(
        "sina_finance",
        "智谱发布GLM-5.3：编程能力跃升50%",
        "https://finance.sina.com.cn/glm-5-3",
        "智谱发布新一代旗舰模型 GLM-5.3，并公布编程能力测试结果。",
    )
    unrelated = _result(
        "market_news",
        "收评：AI板块表现活跃",
        "https://market.example/close",
        "市场关注多个 AI 模型概念股，但没有报道本次模型发布。",
    )
    search = _SequentialSearch([[qq], [qq, sina, unrelated]])
    service = CoverageComparisonService(_Store(), search)

    outcome = asyncio.run(
        service.run(
            topic=topic,
            category_scope=["tech"],
            source_scope=["qq_news", "sina_finance"],
            now=NOW,
        )
    )

    assert [call["category_scope"] for call in search.calls] == [["tech"], None]
    assert all(call["source_scope"] == ["qq_news", "sina_finance"] for call in search.calls)
    assert {item.source_id for item in outcome.data.evidence} == {"qq_news", "sina_finance"}
    assert len(outcome.data.source_groups) == 2


def test_compare_subject_filter_does_not_require_decorative_latin_headline_words() -> None:
    topic = 'GLM-5.3突然发布！唐杰的“sooooooon”这次兑现了'
    items = [
        _result(
            "qq_news",
            topic,
            "https://new.qq.com/glm-5-3",
            "智谱发布 GLM-5.3，唐杰确认新模型已经上线。",
        ),
        _result(
            "sina_finance",
            "智谱发布GLM-5.3：编程能力跃升50%",
            "https://finance.sina.com.cn/glm-5-3",
            "智谱发布新一代旗舰模型 GLM-5.3，并公布编程能力测试结果。",
        ),
    ]
    service = CoverageComparisonService(_Store(), _Search(items))

    outcome = asyncio.run(service.run(topic=topic, now=NOW))

    assert {item.source_id for item in outcome.data.evidence} == {"qq_news", "sina_finance"}
    assert len(outcome.data.source_groups) == 2


def test_compare_subject_filter_prefers_named_entity_over_generic_ai_token() -> None:
    items = [
        _result(
            "openai_news",
            "OpenAI发布新模型",
            "https://openai-news.example/model",
            "OpenAI 公布新一代模型及评测结果。",
        ),
        _result(
            "market_news",
            "AI板块午后走强",
            "https://market.example/ai-stocks",
            "多只 AI 概念股上涨，报道未涉及具体模型发布。",
        ),
    ]
    service = CoverageComparisonService(_Store(), _Search(items))

    outcome = asyncio.run(service.run(topic="AI行业关注OpenAI发布新模型", now=NOW))

    assert [item.source_id for item in outcome.data.evidence] == ["openai_news"]


def test_compare_run_excludes_stale_and_undated_local_evidence() -> None:
    current = _result("current", "当前发布会", "https://current.example/news", "当前同一事件报道")
    stale = _result(
        "stale",
        "旧发布会",
        "https://stale.example/news",
        "数月前的同名事件",
        published_at=NOW - timedelta(days=30),
    )
    undated_local = _result(
        "local-unknown",
        "无日期旧稿",
        "https://local.example/unknown",
        "无法确认时效的本地稿件",
        published_at=None,
    )
    live_external = _result(
        "external-live",
        "实时外部报道",
        "https://external.example/live",
        "当前同一事件的实时报道",
        published_at=None,
        origin="external",
    )
    service = CoverageComparisonService(_Store(), _Search([current, stale, undated_local, live_external]))

    outcome = asyncio.run(service.run(topic="当前发布会", now=NOW))

    assert [item.title for item in outcome.data.evidence] == ["当前发布会", "实时外部报道"]
    assert {group["label"] for group in outcome.data.source_groups} == {"current", "external-live"}


def test_compare_run_excludes_results_missing_topic_entity() -> None:
    items = [
        _result("sports-a", "NBA 公布新赛季完整赛程", "https://a.example/nba", "NBA 常规赛赛程已经公布。"),
        _result("sports-b", "NBA 揭幕战安排出炉", "https://b.example/nba", "NBA 新赛季揭幕战已经确定。"),
        _result("football", "西甲新赛季即将启幕", "https://football.example/laliga", "西甲公布2026-27赛季安排。"),
    ]
    service = CoverageComparisonService(_Store(), _Search(items))

    outcome = asyncio.run(service.run(topic="2026-27赛季NBA常规赛赛程公布", now=NOW))

    assert [item.title for item in outcome.data.evidence] == ["NBA 公布新赛季完整赛程", "NBA 揭幕战安排出炉"]
    assert {group["label"] for group in outcome.data.source_groups} == {"sports-a", "sports-b"}


def test_compare_accepts_one_fenced_json_object_after_explanatory_text() -> None:
    payload = {
        "comparison_status": "sufficient",
        "common_facts": [{"text": "两组均确认9月开放测试", "evidence_indices": [1, 4]}],
        "unique_claims": [],
        "conflicts": [],
        "framing_differences": [],
        "missing_questions": [],
    }
    agent = _Agent(f"分析完成。\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    service = CoverageComparisonService(_Store(), _Search([]), cc_runtime=agent)

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    assert outcome.status == "success"
    assert outcome.fallback_reason is None
    assert outcome.data.common_facts[0]["text"] == "两组均确认9月开放测试"


def test_compare_repairs_unescaped_quotes_inside_agent_json_strings() -> None:
    agent = _Agent(
        '''分析完成。
```json
{
  "comparison_status": "sufficient",
  "common_facts": [
    {"text": "两组均确认产品被称为"智能助手"。", "evidence_indices": [1, 4]}
  ],
  "unique_claims": [],
  "conflicts": [],
  "framing_differences": [],
  "missing_questions": ["各来源所说的"开放测试"是否指同一批用户？"]
}
```
'''
    )
    service = CoverageComparisonService(_Store(), _Search([]), cc_runtime=agent)

    outcome = asyncio.run(service.analyze(topic="Agent 产品", items=_coverage_items()))

    assert outcome.status == "success"
    assert outcome.fallback_reason is None
    assert outcome.data.common_facts[0]["text"] == '两组均确认产品被称为"智能助手"。'
    assert outcome.data.missing_questions == ['各来源所说的"开放测试"是否指同一批用户？']


def test_compare_handler_text_and_structured_inputs_share_service() -> None:
    class _ComparisonService:
        def __init__(self):
            self.calls = []

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            service = CoverageComparisonService(_Store(), _Search([]))
            return await service.analyze(topic=kwargs["topic"], items=_coverage_items()[1:3])

    comparison_service = _ComparisonService()
    context = SkillContext(
        services={"coverage_comparison": comparison_service},
        user_id="user-1",
        conversation_id="conv-1",
        topic="上下文主题",
        allow_web_search=False,
    )
    skill = CompareSkill()

    text_result = asyncio.run(skill.run(["OpenAI", "--source", "official,analysis"], context))
    structured_result = asyncio.run(
        skill.run_structured(CompareArguments(topic=None, category_scope=["tech"]), context)
    )

    assert text_result.output_kind == "coverage_compare"
    assert text_result.data["comparison_status"] == "insufficient"
    assert comparison_service.calls[0]["topic"] == "OpenAI"
    assert comparison_service.calls[0]["source_scope"] == ["official", "analysis"]
    assert comparison_service.calls[1]["topic"] == "上下文主题"
    assert comparison_service.calls[1]["category_scope"] == ["tech"]


def test_compare_is_enabled_in_manifest_and_registry() -> None:
    definition = next(item for item in all_definitions() if item.id == "compare")
    registry = build_default_registry()

    assert definition.enabled is True
    assert definition.handler == "personal_news_agent.skills.compare:CompareSkill"
    assert "/compare" in {item.command for item in registry.list_skills()}
