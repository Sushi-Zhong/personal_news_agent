from __future__ import annotations

from dataclasses import replace

import pytest

from personal_news_agent.skills.catalog import all_definitions
from personal_news_agent.skills.manifest import (
    SkillDefinition,
    public_projection,
    validate_catalog,
    validate_service_bindings,
)
from personal_news_agent.services.cc_runtime import allowed_project_skills


def test_catalog_declares_public_roles_and_legacy_aliases() -> None:
    definitions = {item.id: item for item in all_definitions()}

    assert {"brief", "factcheck", "map", "report", "changed", "compare", "schedule"} <= definitions.keys()
    assert definitions["related"].exposure == "context_action"
    assert definitions["sources"].exposure == "admin"
    assert definitions["news-conversation-research"].exposure == "internal"
    assert "/r" in definitions["report"].aliases
    assert "/verify" in definitions["factcheck"].aliases
    assert "/graph" in definitions["map"].aliases


def test_catalog_validation_rejects_duplicate_commands_and_aliases() -> None:
    base = all_definitions()[0]
    duplicate = replace(base, id="duplicate", commands=base.commands)

    with pytest.raises(ValueError, match="duplicate.*command"):
        validate_catalog([base, duplicate])

    alias_duplicate = replace(base, id="alias-duplicate", commands=("/different",), aliases=base.aliases)
    with pytest.raises(ValueError, match="duplicate.*alias"):
        validate_catalog([base, alias_duplicate])


def test_catalog_validation_rejects_orphan_handler_and_agent_skill(tmp_path) -> None:
    orphan_handler = SkillDefinition(
        id="orphan-handler",
        name="orphan",
        commands=("/orphan",),
        aliases=(),
        exposure="public",
        description="orphan",
        intent_examples=(),
        handler="missing.module:Handler",
        arguments_model=None,
        agent_skill=None,
        required_services=(),
        network_policy="local_only",
        side_effect="read_only",
        confirmation_required=False,
        output_kind="default_markdown",
        citation_policy="none",
        fallback_policy="default_markdown",
        enabled=True,
    )
    with pytest.raises(ValueError, match="orphan-handler.*handler"):
        validate_catalog([orphan_handler])

    orphan_agent = replace(
        all_definitions()[0],
        id="orphan-agent",
        commands=("/orphan-agent",),
        agent_skill="skill-that-does-not-exist",
    )
    with pytest.raises(ValueError, match="orphan-agent.*Agent Skill"):
        validate_catalog([orphan_agent])


def test_catalog_validation_rejects_enabled_handler_without_required_services() -> None:
    definition = replace(
        all_definitions()[0],
        id="missing-services",
        commands=("/missing-services",),
        required_services=(),
    )

    with pytest.raises(ValueError, match="missing-services.*required_services"):
        validate_catalog([definition], validate_files=False)


def test_service_binding_validation_names_missing_skill_and_service() -> None:
    definition = replace(
        all_definitions()[0],
        id="missing-binding",
        commands=("/missing-binding",),
        required_services=("reports", "missing-service"),
    )

    with pytest.raises(ValueError, match="missing-binding.*missing-service"):
        validate_service_bindings([definition], {"reports": object()})


def test_public_projection_does_not_expose_internal_runtime_fields() -> None:
    item = next(item for item in all_definitions() if item.id == "report")

    projection = public_projection(item)

    assert projection["id"] == "report"
    assert projection["commands"] == ["/report"]
    assert projection["exposure"] == "public"
    assert "handler" not in projection
    assert "agent_skill" not in projection
    assert "required_services" not in projection
    assert "fallback_policy" not in projection


def test_cc_runtime_allow_list_is_derived_from_enabled_catalog() -> None:
    expected = {
        item.agent_skill
        for item in all_definitions()
        if item.enabled and item.agent_skill
    }

    assert allowed_project_skills() == frozenset(expected)
