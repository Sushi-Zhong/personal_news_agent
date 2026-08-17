from pathlib import Path

import pytest

from personal_news_agent.services.source_registry import SourceRegistryError, SourceRegistryService
from personal_news_agent.skills.base import SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.manifest import SkillDefinition
from personal_news_agent.skills.registry import SkillRegistry


class _RecordingSkill:
    spec = SkillSpec(
        command="/report",
        name="recording",
        description="recording",
        usage="/report [topic]",
    )

    def __init__(self) -> None:
        self.calls: list[object] = []
        self.contexts: list[SkillContext] = []

    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        self.calls.append(args)
        self.contexts.append(context)
        return SkillResult(command=self.spec.command, title="ok", message="ok", data={"args": args})


class _StructuredSkill(_RecordingSkill):
    spec = SkillSpec(
        command="/changed",
        name="changed",
        description="changed",
        usage="/changed [topic]",
    )

    async def run_structured(self, arguments, context: SkillContext) -> SkillResult:
        self.calls.append(arguments)
        return SkillResult(
            command=self.spec.command,
            title="ok",
            message="ok",
            data=arguments.model_dump(mode="json"),
        )


class _EvidenceSkill(_RecordingSkill):
    async def run(self, args: list[str], context: SkillContext) -> SkillResult:
        return SkillResult(
            command=self.spec.command,
            title="ok",
            message="ok",
            data={
                "evidence": [
                    {
                        "title": "实际取得的证据",
                        "url": "https://example.com/story?utm_source=test",
                        "source_id": "example",
                        "origin": "local",
                    },
                    {"title": "无效模型来源", "url": "invented-without-host"},
                ],
                "sources": [
                    {
                        "title": "同一证据",
                        "url": "https://example.com/story",
                        "source_id": "example",
                        "origin": "local",
                    }
                ],
            },
        )


def _definition(**overrides) -> SkillDefinition:
    values = {
        "id": "report",
        "name": "report",
        "commands": ("/report",),
        "aliases": ("/r",),
        "exposure": "public",
        "description": "report",
        "intent_examples": (),
        "handler": None,
        "arguments_model": None,
        "agent_skill": None,
        "required_services": (),
        "network_policy": "local_only",
        "side_effect": "read_only",
        "confirmation_required": False,
        "output_kind": "default_markdown",
        "citation_policy": "none",
        "fallback_policy": "default_markdown",
        "enabled": True,
    }
    values.update(overrides)
    return SkillDefinition(**values)


@pytest.mark.asyncio
async def test_skill_registry_execute_text_supports_legacy_alias() -> None:
    skill = _RecordingSkill()
    registry = SkillRegistry([skill], definitions=[_definition()])

    result = await registry.execute_text('/r "AI Agent"', SkillContext(services={}))

    assert result.command == "/report"
    assert result.data["args"] == ["AI Agent"]
    assert skill.calls == [["AI Agent"]]


@pytest.mark.asyncio
async def test_skill_registry_execute_delegates_to_execute_text() -> None:
    skill = _RecordingSkill()
    registry = SkillRegistry([skill], definitions=[_definition()])

    result = await registry.execute("/report AI", SkillContext(services={}))

    assert result.data["args"] == ["AI"]


@pytest.mark.asyncio
async def test_skill_registry_execute_structured_validates_and_calls_structured_handler() -> None:
    skill = _StructuredSkill()
    definition = _definition(
        id="changed",
        commands=("/changed",),
        aliases=(),
        arguments_model="personal_news_agent.skills.arguments:ChangedArguments",
        output_kind="change_digest",
    )
    registry = SkillRegistry([skill], definitions=[definition])

    result = await registry.execute_structured(
        "changed",
        {"topic": "OpenAI", "baseline_expression": "昨天"},
        SkillContext(services={}),
    )

    assert result.data["topic"] == "OpenAI"
    assert result.data["baseline_expression"] == "昨天"
    assert skill.calls[0].topic == "OpenAI"
    assert result.skill_id == "changed"
    assert result.output_kind == "change_digest"


@pytest.mark.asyncio
async def test_skill_registry_execute_text_applies_manifest_output_kind() -> None:
    skill = _RecordingSkill()
    definition = _definition(output_kind="topic_report")
    registry = SkillRegistry([skill], definitions=[definition])

    result = await registry.execute_text("/report AI", SkillContext(services={}))

    assert result.skill_id == "report"
    assert result.output_kind == "topic_report"


@pytest.mark.asyncio
async def test_skill_registry_enforces_local_only_network_policy() -> None:
    skill = _RecordingSkill()
    registry = SkillRegistry([skill], definitions=[_definition(network_policy="local_only")])

    await registry.execute_text(
        "/report AI",
        SkillContext(services={}, allow_web_search=True),
    )

    assert skill.contexts[0].allow_web_search is False


@pytest.mark.asyncio
async def test_skill_registry_projects_observed_handler_urls_into_unified_evidence() -> None:
    registry = SkillRegistry([_EvidenceSkill()], definitions=[_definition(citation_policy="evidence_required")])

    result = await registry.execute_text("/report AI", SkillContext(services={}))

    assert len(result.evidence) == 1
    assert result.evidence[0].index == 1
    assert result.evidence[0].url == "https://example.com/story"
    assert result.evidence[0].claim_role == "context"


@pytest.mark.asyncio
async def test_skill_registry_execute_structured_rejects_extra_arguments() -> None:
    skill = _StructuredSkill()
    definition = _definition(
        id="changed",
        commands=("/changed",),
        aliases=(),
        arguments_model="personal_news_agent.skills.arguments:ChangedArguments",
    )
    registry = SkillRegistry([skill], definitions=[definition])

    with pytest.raises(ValueError, match="changed.*arguments"):
        await registry.execute_structured(
            "changed",
            {"topic": "OpenAI", "unexpected": True},
            SkillContext(services={}),
        )


def test_registry_loads_focused_categories_and_sources():
    registry = SourceRegistryService(Path("sources.yaml"))
    registry.load()

    assert len(registry.all_sources()) >= 20
    assert len(registry.get_sources_by_category("tech")) >= 4
    assert len(registry.get_sections_by_category("game")) >= 4
    assert "ithome.com" in registry.get_domain_filters(["tech"], None)
    summary = registry.source_summary()
    assert summary["source_count"] >= 20
    assert summary["tags"]["tech"] >= 4
    ithome = registry.get_source("ithome")
    assert "tech" in ithome.tags
    assert ithome.crawl_interval_minutes > 0
    assert registry.get_source("people_politics").crawl_interval_minutes == 10
    assert len(registry.get_sources_by_category("digital")) >= 2
    assert len(registry.get_sources_by_category("military")) >= 3
    assert len(registry.get_sources_by_category("sports")) >= 7
    assert len(registry.get_sections_by_category("economy")) >= 12
    assert len(registry.get_sections_by_category("sports")) >= 14
    assert len(registry.get_sections_by_category("digital")) >= 8

    sina_sports = registry.get_source("sina_sports")
    assert {section.key for section in sina_sports.sections} >= {"sports", "nba", "global_football", "china_football"}
    assert registry.get_source("sohu_military").sections[0].crawl_enabled


def test_registry_keeps_portal_channel_pages_separate_from_discovery_feeds():
    registry = SourceRegistryService(Path("sources.yaml"))
    registry.load()

    qq = registry.get_source("qq_news")
    toutiao = registry.get_source("toutiao")

    assert len(qq.sections) == 11
    assert len(toutiao.sections) == 9
    assert all(section.url.startswith("https://news.qq.com/ch/") for section in qq.sections)
    assert all(section.discovery_url and section.crawl_strategy == "json_feed" for section in (*qq.sections, *toutiao.sections))


def test_registry_rejects_invalid_category(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        """
sources:
  - source_id: bad
    name: Bad
    root_domain: example.com
    source_type: portal
    categories: [unknown]
    sections:
      - key: unknown
        name: Bad
        category: unknown
        url: https://example.com/
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        SourceRegistryService(path).load()


def test_registry_rejects_bad_url(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text(
        """
sources:
  - source_id: bad
    name: Bad
    root_domain: example.com
    source_type: portal
    categories: [tech]
    sections:
      - key: tech
        name: Bad
        category: tech
        url: not-a-url
""",
        encoding="utf-8",
    )

    with pytest.raises(SourceRegistryError):
        SourceRegistryService(path).load()


def test_registry_selects_sources_for_profile():
    registry = SourceRegistryService(Path("sources.yaml"))
    registry.load()
    selected = registry.select_sources_for_profile(
        {
            "preferred_categories": ["sports"],
            "interests": ["NBA"],
            "self_description": "关心体育",
        }
    )
    assert selected
    assert all("sports" in source.categories for source in selected)
