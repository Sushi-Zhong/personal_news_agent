from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from personal_news_agent.core.models import EvidenceRef
from personal_news_agent.services.skill_router import SkillRouter, SkillRoutingContext
from personal_news_agent.services.chat import NewsChatService
from personal_news_agent.skills.base import SkillResult
from personal_news_agent.services.skill_router import SkillRoute
from personal_news_agent.skills.catalog import all_definitions


class _FakeLLM:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {"skill_id": None, "arguments": {}, "confidence": 0.0}
        self.calls: list[dict] = []

    async def structured(self, messages, schema_name, schema, model_key=None):
        self.calls.append({"messages": messages, "schema_name": schema_name, "schema": schema, "model_key": model_key})
        return self.payload


def _definitions():
    return [replace(item, enabled=True) if item.id in {"changed", "compare"} else item for item in all_definitions()]


@pytest.mark.asyncio
async def test_skill_route_arguments_have_independent_default_dicts() -> None:
    router = SkillRouter(_FakeLLM(), definitions=_definitions())

    first = await router.route("你好", context=SkillRoutingContext(), model_key="default", use_llm=False)
    second = await router.route("你好", context=SkillRoutingContext(), model_key="default", use_llm=False)
    first.arguments["mutated"] = True

    assert second.arguments == {}


@pytest.mark.asyncio
async def test_router_routes_explicit_slash_without_model() -> None:
    llm = _FakeLLM()
    router = SkillRouter(llm, definitions=_definitions())

    route = await router.route(
        "/verify OpenAI 发布了新模型",
        context=SkillRoutingContext(topic="OpenAI"),
        model_key="custom-model",
        use_llm=True,
    )

    assert route.skill_id == "factcheck"
    assert route.source == "slash_command"
    assert route.confidence == 1.0
    assert llm.calls == []


@pytest.mark.asyncio
async def test_router_extracts_changed_and_schedule_arguments_from_high_precision_rules() -> None:
    router = SkillRouter(_FakeLLM(), definitions=_definitions())

    changed = await router.route(
        "OpenAI 这件事从昨天到现在有什么新进展？",
        context=SkillRoutingContext(),
        model_key="default",
        use_llm=False,
    )
    schedule = await router.route(
        "每天早上九点给我推送 AI Agent 新闻",
        context=SkillRoutingContext(),
        model_key="default",
        use_llm=False,
    )

    assert changed.skill_id == "changed"
    assert changed.arguments["baseline_expression"] == "昨天"
    assert "OpenAI" in changed.arguments["topic"]
    assert schedule.skill_id == "schedule"
    assert schedule.arguments == {"raw_request": "每天早上九点给我推送 AI Agent 新闻"}
    assert schedule.confirmation_required is True


@pytest.mark.asyncio
async def test_router_does_not_misroute_non_news_comparison() -> None:
    llm = _FakeLLM({"skill_id": "compare", "arguments": {"topic": "两句话"}, "confidence": 0.99})
    router = SkillRouter(llm, definitions=_definitions())

    route = await router.route(
        "帮我比较一下这两句话的语法差异",
        context=SkillRoutingContext(),
        model_key="default",
        use_llm=True,
    )

    assert route.skill_id is None
    assert route.source == "fallback"
    assert llm.calls == []


@pytest.mark.asyncio
async def test_router_respects_use_llm_and_forwards_request_model_key() -> None:
    disabled_llm = _FakeLLM({"skill_id": "report", "arguments": {"topic": "芯片"}, "confidence": 0.95})
    disabled_router = SkillRouter(disabled_llm, definitions=_definitions())

    disabled = await disabled_router.route(
        "整理一下芯片领域近期情况",
        context=SkillRoutingContext(),
        model_key="chosen-model",
        use_llm=False,
    )

    assert disabled.skill_id is None
    assert disabled_llm.calls == []

    enabled_llm = _FakeLLM({"skill_id": "report", "arguments": {"topic": "芯片"}, "confidence": 0.95})
    enabled_router = SkillRouter(enabled_llm, definitions=_definitions())
    enabled = await enabled_router.route(
        "整理一下芯片领域近期情况",
        context=SkillRoutingContext(),
        model_key="chosen-model",
        use_llm=True,
    )

    assert enabled.skill_id == "report"
    assert enabled.source == "model"
    assert enabled_llm.calls[0]["model_key"] == "chosen-model"


@pytest.mark.asyncio
async def test_router_falls_back_when_model_confidence_is_low() -> None:
    llm = _FakeLLM({"skill_id": "report", "arguments": {"topic": "芯片"}, "confidence": 0.4})
    router = SkillRouter(llm, definitions=_definitions())

    route = await router.route(
        "整理一下芯片领域近期情况",
        context=SkillRoutingContext(),
        model_key="default",
        use_llm=True,
    )

    assert route.skill_id is None
    assert route.source == "fallback"


class _RouteStub:
    def __init__(self, route: SkillRoute) -> None:
        self.value = route
        self.calls: list[dict] = []

    async def route(self, message, *, context, model_key, use_llm):
        self.calls.append({"message": message, "context": context, "model_key": model_key, "use_llm": use_llm})
        return self.value


class _RegistryStub:
    def __init__(self) -> None:
        self.text_calls: list[str] = []
        self.structured_calls: list[tuple[str, dict]] = []

    async def execute_text(self, text, context):
        self.text_calls.append(text)
        return SkillResult(command="/brief", skill_id="brief", title="Brief", message="done")

    async def execute_structured(self, skill_id, arguments, context):
        self.structured_calls.append((skill_id, arguments))
        return SkillResult(
            command="/brief",
            skill_id="brief",
            title="Brief",
            message="done",
            status="degraded",
            output_kind="news_brief",
            fallback_reason="agent_unavailable",
            evidence=(
                EvidenceRef(
                    index=1,
                    title="证据",
                    url="https://example.com/evidence",
                    source_id="source",
                    origin="local",
                    claim_role="supporting",
                ),
            ),
        )


class _StoreStub:
    def list_turns(self, *args, **kwargs):
        return []

    def save_turn(self, *args, **kwargs):
        return "turn-1"

    def update_turn_response(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_chat_shared_skill_execution_uses_structured_registry_and_envelope() -> None:
    route_stub = _RouteStub(
        SkillRoute(
            skill_id="brief",
            arguments={"topic": "芯片"},
            confidence=0.95,
            source="rule",
        )
    )
    registry = _RegistryStub()
    chat = NewsChatService(
        _StoreStub(),
        search_service=None,
        skill_registry=registry,
        skill_router=route_stub,
        services={},
    )

    response = await chat._route_and_execute_skill(
        "conv",
        "给我一份芯片新闻简报",
        "user",
        "芯片",
        ["tech"],
        allow_web_search=False,
        model_key="chosen-model",
        use_llm=True,
    )

    assert registry.structured_calls == [("brief", {"topic": "芯片"})]
    assert registry.text_calls == []
    assert route_stub.calls[0]["model_key"] == "chosen-model"
    assert route_stub.calls[0]["use_llm"] is True
    assert response.status == "degraded"
    assert response.output_kind == "news_brief"
    assert response.fallback_reason == "agent_unavailable"
    assert response.skill_result["skill_id"] == "brief"
    assert response.skill_result["evidence"][0]["index"] == 1
    assert response.evidence[0]["url"] == "https://example.com/evidence"


@pytest.mark.asyncio
async def test_chat_shared_skill_execution_keeps_slash_text_path() -> None:
    route_stub = _RouteStub(
        SkillRoute(skill_id="brief", confidence=1.0, source="slash_command")
    )
    registry = _RegistryStub()
    chat = NewsChatService(
        _StoreStub(),
        search_service=None,
        skill_registry=registry,
        skill_router=route_stub,
        services={},
    )

    response = await chat._route_and_execute_skill(
        "conv",
        "/brief 芯片",
        "user",
        "芯片",
        ["tech"],
        allow_web_search=False,
        model_key="chosen-model",
        use_llm=False,
    )

    assert response is not None
    assert registry.text_calls == ["/brief 芯片"]
    assert registry.structured_calls == []


@pytest.mark.asyncio
async def test_chat_events_yields_skill_trace_before_blocking_handler_finishes() -> None:
    emitted = asyncio.Event()
    release = asyncio.Event()

    class _BlockingRegistry:
        async def execute_text(self, text, context):
            await context.on_trace(
                {"stage": "阻塞 Skill", "status": "running", "message": "已开始处理。"}
            )
            emitted.set()
            await release.wait()
            return SkillResult(command="/brief", title="Brief", message="done")

    route_stub = _RouteStub(
        SkillRoute(skill_id="brief", confidence=1.0, source="slash_command")
    )
    chat = NewsChatService(
        _StoreStub(),
        search_service=None,
        skill_registry=_BlockingRegistry(),
        skill_router=route_stub,
        services={},
    )
    events = chat.chat_events("conv", "/brief 芯片", user_id="user")

    assert (await anext(events))["type"] == "start"
    next_event = asyncio.create_task(anext(events))
    await asyncio.wait_for(emitted.wait(), timeout=0.5)
    trace = await asyncio.wait_for(next_event, timeout=0.5)

    assert trace == {
        "type": "trace",
        "item": {"stage": "场景 Skill", "status": "running", "message": "正在执行 /brief 场景工作流。"},
    }
    release.set()
    remaining = [event async for event in events]
    assert remaining[-1]["type"] == "final"
