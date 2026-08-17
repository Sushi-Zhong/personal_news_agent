from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from personal_news_agent.core.models import SearchResult
from personal_news_agent.services.cc_runtime import CCRuntimeResult
from personal_news_agent.services.change_detection import ChangeDetectionService, resolve_change_baseline
from personal_news_agent.skills.arguments import ChangedArguments
from personal_news_agent.skills.base import SkillContext
from personal_news_agent.skills.changed import ChangedSkill
from personal_news_agent.skills.catalog import all_definitions
from personal_news_agent.skills.registry import build_default_registry


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def _result(title: str, url: str, summary: str, *, published_at: datetime | None = None) -> SearchResult:
    return SearchResult(
        source_id="source",
        title=title,
        url=url,
        summary=summary,
        category="tech",
        published_at=published_at or NOW,
        score=1.0,
        origin="local",
    )


class _Store:
    def __init__(self, turns=None):
        self.turns = turns or []

    def list_turns(self, conversation_id, user_id, limit=40):
        return list(self.turns)

    def log(self, *args, **kwargs):
        return None


class _Search:
    def __init__(self, items):
        self.items = items
        self.calls = []

    async def search(self, query, category_scope, source_scope, time_range, max_results=20, include_remote=False):
        self.calls.append({"query": query, "include_remote": include_remote, "days": time_range.days})
        return list(self.items)


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


def test_changed_without_history_uses_explicit_past_24_hours_baseline() -> None:
    label, baseline_at = resolve_change_baseline(None, now=NOW, app_timezone=ZoneInfo("Asia/Shanghai"))

    assert label == "默认：过去24小时"
    assert baseline_at == NOW - timedelta(hours=24)
    assert baseline_at.utcoffset() == timedelta(hours=8)


def test_changed_user_range_overrides_default_baseline() -> None:
    label, baseline_at = resolve_change_baseline("昨天", now=NOW, app_timezone=ZoneInfo("Asia/Shanghai"))

    assert label == "用户指定：昨天 00:00（Asia/Shanghai）"
    assert baseline_at == datetime(2026, 8, 13, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_changed_parses_month_day_and_expands_retrieval_window() -> None:
    search = _Search([])
    service = ChangeDetectionService(_Store(), search, app_timezone=ZoneInfo("Asia/Shanghai"))

    outcome = asyncio.run(
        service.run(
            user_id="user-1",
            conversation_id=None,
            topic="OpenAI",
            baseline_expression="自 8 月 1 日",
            now=NOW,
        )
    )

    assert outcome.data.baseline_at == datetime(2026, 8, 1, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert outcome.data.baseline_label == "用户指定：2026-08-01 00:00（Asia/Shanghai）"
    assert search.calls[0]["days"] == 14


def test_changed_run_excludes_results_missing_topic_entity() -> None:
    relevant = _result(
        "NBA 公布新赛季完整赛程",
        "https://sports.example/nba",
        "NBA 常规赛赛程已经公布。",
    )
    unrelated = _result(
        "西甲新赛季即将启幕",
        "https://sports.example/laliga",
        "西甲公布2026-27赛季安排。",
    )
    service = ChangeDetectionService(
        _Store(),
        _Search([relevant, unrelated]),
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )

    outcome = asyncio.run(
        service.run(
            user_id="user-1",
            conversation_id=None,
            topic="2026-27赛季NBA常规赛赛程公布",
            now=NOW,
        )
    )

    assert [item.title for item in outcome.data.evidence] == ["NBA 公布新赛季完整赛程"]


def test_changed_rejects_unparseable_explicit_baseline() -> None:
    service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))

    with pytest.raises(ValueError, match="无法解析.*基线"):
        asyncio.run(
            service.run(
                user_id="user-1",
                conversation_id=None,
                topic="OpenAI",
                baseline_expression="从很久以前",
                now=NOW,
            )
        )


def test_changed_treats_same_copy_with_new_title_as_repeated_report() -> None:
    service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))
    baseline = [_result("OpenAI 发布 Agent SDK", "https://wire.example/a", "OpenAI 发布 Agent SDK 1.0，支持工具调用")]
    current = [_result("重磅！OpenAI Agent SDK 正式来了", "https://portal.example/b", "OpenAI 发布 Agent SDK 1.0，支持工具调用")]

    outcome = asyncio.run(
        service.analyze(
            topic="OpenAI Agent SDK",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：昨天的研究结果",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert outcome.status == "success"
    assert outcome.data.change_status == "no_material_change"
    assert outcome.data.new_facts == []
    assert outcome.data.repeated_reports[0]["evidence_indices"] == [1, 2]


def test_changed_canonical_url_variant_does_not_duplicate_evidence_index() -> None:
    service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))
    baseline = [_result("原稿", "https://example.com/story?utm_source=a", "完全相同的报道正文")]
    current = [_result("原稿新标题", "https://example.com/story?utm_campaign=b", "完全相同的报道正文")]

    outcome = asyncio.run(
        service.analyze(
            topic="同一报道",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：昨天的研究结果",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert len(outcome.data.evidence) == 1
    assert outcome.data.repeated_reports[0]["evidence_indices"] == [1]


def test_changed_keeps_two_snapshots_when_same_url_content_changes() -> None:
    agent = _Agent(
        {
            "change_status": "changed",
            "new_facts": [],
            "status_changes": [{"text": "测试从未开始变为已开始", "evidence_indices": [1, 2]}],
            "number_changes": [{"text": "参与人数从10人变为100人", "evidence_indices": [1, 2]}],
            "corrections": [],
            "watch_next": [],
        }
    )
    service = ChangeDetectionService(
        _Store(),
        _Search([]),
        cc_runtime=agent,
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )
    baseline = [_result("测试进度", "https://example.com/live", "测试尚未开始，计划招募10人")]
    current = [_result("测试进度", "https://example.com/live", "测试已经开始，参与人数增至100人")]

    outcome = asyncio.run(
        service.analyze(
            topic="测试进度",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：旧研究",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert [item.index for item in outcome.data.evidence] == [1, 2]
    assert [item.claim_role for item in outcome.data.evidence] == ["baseline", "current"]
    assert outcome.data.status_changes[0]["evidence_indices"] == [1, 2]
    assert outcome.data.number_changes[0]["evidence_indices"] == [1, 2]
    request_payload = json.loads(agent.calls[0]["message"].split("\n", 1)[1])
    assert [item["summary"] for item in request_payload["evidence"]] == [
        "测试尚未开始，计划招募10人",
        "测试已经开始，参与人数增至100人",
    ]


def test_changed_uses_content_hash_to_recognize_same_copy() -> None:
    service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))
    baseline = [
        {
            "title": "旧标题",
            "url": "https://example.com/old",
            "source_id": "wire",
            "summary": "",
            "content_hash": "same-content-hash",
            "origin": "local",
        }
    ]
    current = [
        {
            "title": "新标题",
            "url": "https://portal.example/new",
            "source_id": "portal",
            "summary": "",
            "content_hash": "same-content-hash",
            "origin": "local",
        }
    ]

    outcome = asyncio.run(
        service.analyze(
            topic="同稿",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：旧研究",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert outcome.data.change_status == "no_material_change"
    assert outcome.data.repeated_reports


def test_changed_agent_unavailable_only_reports_unclassified_new_reports() -> None:
    service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))
    baseline = [_result("OpenAI 宣布测试", "https://example.com/base", "OpenAI 宣布开始内部测试")]
    current = [_result("OpenAI 扩大测试", "https://example.com/current", "OpenAI 将测试范围扩大至更多用户")]

    outcome = asyncio.run(
        service.analyze(
            topic="OpenAI 测试",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：昨天的研究结果",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert outcome.status == "degraded"
    assert outcome.fallback_reason == "agent_unavailable"
    assert outcome.data.change_status == "insufficient"
    assert outcome.data.status_changes == []
    assert outcome.data.repeated_reports[0]["classification"] == "unclassified_new_report"
    assert outcome.data.repeated_reports[0]["evidence_indices"] == [2]


def test_changed_agent_output_keeps_valid_ledger_indices_and_requires_current_evidence() -> None:
    agent = _Agent(
        {
            "change_status": "changed",
            "new_facts": [{"text": "测试范围扩大", "evidence_indices": [1, 2, 999]}],
            "status_changes": [],
            "number_changes": [],
            "corrections": [],
            "watch_next": ["观察正式开放"],
        }
    )
    service = ChangeDetectionService(
        _Store(),
        _Search([]),
        cc_runtime=agent,
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )
    baseline = [_result("OpenAI 宣布测试", "https://example.com/base", "OpenAI 宣布开始内部测试")]
    current = [_result("OpenAI 扩大测试", "https://example.com/current", "OpenAI 将测试范围扩大至更多用户")]

    outcome = asyncio.run(
        service.analyze(
            topic="OpenAI 测试",
            baseline_items=baseline,
            current_items=current,
            baseline_label="当前对话：昨天的研究结果",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert outcome.status == "success"
    assert outcome.data.change_status == "changed"
    assert outcome.data.new_facts[0]["evidence_indices"] == [1, 2]
    assert [item.index for item in outcome.data.evidence] == [1, 2]
    assert agent.calls[0]["allow_web_search"] is False


def test_changed_prefers_relevant_conversation_evidence_when_no_range_is_given() -> None:
    baseline_time = datetime(2026, 8, 13, 2, 0, tzinfo=timezone.utc)
    turns = [
        {
            "created_at": baseline_time.isoformat(),
            "response": {
                "topic": "OpenAI Agent SDK",
                "evidence": [
                    {
                        "title": "OpenAI 发布 Agent SDK",
                        "url": "https://example.com/base",
                        "source_id": "source",
                        "summary": "OpenAI 发布 Agent SDK 1.0",
                        "origin": "local",
                    }
                ],
            },
        }
    ]
    current = [_result("OpenAI 扩大 SDK 测试", "https://example.com/current", "OpenAI 扩大 Agent SDK 测试")]
    service = ChangeDetectionService(_Store(turns), _Search(current), app_timezone=ZoneInfo("Asia/Shanghai"))

    outcome = asyncio.run(
        service.run(
            user_id="user-1",
            conversation_id="conv-1",
            topic="OpenAI Agent SDK",
            now=NOW,
        )
    )

    assert outcome.data.baseline_label.startswith("当前对话：")
    assert outcome.data.baseline_at == baseline_time
    assert outcome.data.evidence[0].claim_role == "baseline"


def test_changed_uses_seven_day_snapshot_window_for_recent_conversation_baseline() -> None:
    baseline_time = NOW - timedelta(minutes=10)
    article_time = NOW - timedelta(days=3)
    shared_summary = "OpenAI Agent SDK 当前稳定版本"
    turns = [
        {
            "created_at": baseline_time.isoformat(),
            "response": {
                "topic": "OpenAI Agent SDK",
                "output_kind": "default_markdown",
                "evidence": [
                    {
                        "title": "三天前的研究证据",
                        "url": "https://example.com/shared",
                        "source_id": "source",
                        "summary": shared_summary,
                        "published_at": article_time.isoformat(),
                        "origin": "local",
                    }
                ],
            },
        }
    ]
    search = _Search(
        [
            _result(
                "三天前的研究证据",
                "https://example.com/shared",
                shared_summary,
                published_at=article_time,
            )
        ]
    )
    service = ChangeDetectionService(_Store(turns), search, app_timezone=ZoneInfo("Asia/Shanghai"))

    outcome = asyncio.run(
        service.run(user_id="user-1", conversation_id="conv-1", topic="OpenAI Agent SDK", now=NOW)
    )

    assert search.calls[0]["days"] == 7
    assert outcome.data.change_status == "no_material_change"


def test_changed_skips_derived_skill_results_when_selecting_conversation_baseline() -> None:
    research_time = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
    compare_time = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
    turns = [
        {
            "created_at": research_time.isoformat(),
            "response": {
                "topic": "OpenAI Agent SDK",
                "output_kind": "default_markdown",
                "evidence": [
                    {
                        "title": "最初研究快照",
                        "url": "https://example.com/research",
                        "source_id": "source",
                        "summary": "OpenAI Agent SDK 最初研究证据",
                        "origin": "local",
                    }
                ],
            },
        },
        {
            "created_at": compare_time.isoformat(),
            "response": {
                "topic": "OpenAI Agent SDK",
                "output_kind": "coverage_compare",
                "evidence": [
                    {
                        "title": "派生对比结果",
                        "url": "https://example.com/compare",
                        "source_id": "source",
                        "summary": "OpenAI Agent SDK 对比证据",
                        "origin": "local",
                    }
                ],
            },
        },
    ]
    current = [_result("当前研究", "https://example.com/current", "OpenAI Agent SDK 当前证据")]
    service = ChangeDetectionService(_Store(turns), _Search(current), app_timezone=ZoneInfo("Asia/Shanghai"))

    outcome = asyncio.run(
        service.run(user_id="user-1", conversation_id="conv-1", topic="OpenAI Agent SDK", now=NOW)
    )

    assert outcome.data.baseline_at == research_time
    assert outcome.data.evidence[0].title == "最初研究快照"


def test_changed_accepts_one_fenced_json_object_after_explanatory_text() -> None:
    payload = {
        "change_status": "changed",
        "new_facts": [{"text": "测试范围扩大", "evidence_indices": [2]}],
        "status_changes": [],
        "number_changes": [],
        "corrections": [],
        "watch_next": [],
    }
    agent = _Agent(f"核对完成。\n```json\n{json.dumps(payload, ensure_ascii=False)}\n```")
    service = ChangeDetectionService(
        _Store(),
        _Search([]),
        cc_runtime=agent,
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )

    outcome = asyncio.run(
        service.analyze(
            topic="OpenAI 测试",
            baseline_items=[_result("旧范围", "https://example.com/base", "测试仅面向内部")],
            current_items=[_result("新范围", "https://example.com/current", "测试范围扩大")],
            baseline_label="当前对话：旧研究",
            baseline_at=NOW - timedelta(days=1),
        )
    )

    assert outcome.status == "success"
    assert outcome.fallback_reason is None
    assert outcome.data.new_facts[0]["text"] == "测试范围扩大"


def test_changed_handler_text_and_structured_inputs_share_service() -> None:
    class _ChangeService:
        def __init__(self):
            self.calls = []

        async def run(self, **kwargs):
            self.calls.append(kwargs)
            service = ChangeDetectionService(_Store(), _Search([]), app_timezone=ZoneInfo("Asia/Shanghai"))
            return await service.analyze(
                topic=kwargs["topic"],
                baseline_items=[_result("旧稿", "https://example.com/base", "相同正文")],
                current_items=[_result("新标题", "https://example.com/current", "相同正文")],
                baseline_label="默认：过去24小时",
                baseline_at=NOW - timedelta(hours=24),
            )

    change_service = _ChangeService()
    context = SkillContext(
        services={"change_detection": change_service},
        user_id="user-1",
        conversation_id="conv-1",
        topic="上下文主题",
        allow_web_search=False,
    )
    skill = ChangedSkill()

    text_result = asyncio.run(skill.run(["OpenAI", "--baseline", "昨天"], context))
    structured_result = asyncio.run(
        skill.run_structured(ChangedArguments(topic=None, baseline_expression="过去一周"), context)
    )

    assert text_result.output_kind == "change_digest"
    assert text_result.data["change_status"] == "no_material_change"
    assert change_service.calls[0]["topic"] == "OpenAI"
    assert change_service.calls[0]["baseline_expression"] == "昨天"
    assert change_service.calls[1]["topic"] == "上下文主题"
    assert change_service.calls[1]["baseline_expression"] == "过去一周"


def test_changed_is_enabled_in_manifest_and_registry() -> None:
    definition = next(item for item in all_definitions() if item.id == "changed")
    registry = build_default_registry()

    assert definition.enabled is True
    assert definition.handler == "personal_news_agent.skills.changed:ChangedSkill"
    assert "/changed" in {item.command for item in registry.list_skills()}
