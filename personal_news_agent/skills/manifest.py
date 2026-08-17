from __future__ import annotations

from dataclasses import dataclass
import importlib
from pathlib import Path
from typing import Any, Literal


Exposure = Literal["public", "internal", "admin", "context_action"]
NetworkPolicy = Literal["local_only", "user_controlled"]
SideEffect = Literal["read_only", "creates_task", "updates_state", "admin_write"]
CitationPolicy = Literal["none", "evidence_if_claims", "evidence_required"]
FallbackPolicy = Literal["local_service", "local_evidence_only", "default_markdown", "blocked"]


@dataclass(frozen=True)
class SkillDefinition:
    id: str
    name: str
    commands: tuple[str, ...]
    aliases: tuple[str, ...]
    exposure: Exposure
    description: str
    intent_examples: tuple[str, ...]
    handler: str | None
    arguments_model: str | None
    agent_skill: str | None
    required_services: tuple[str, ...]
    network_policy: NetworkPolicy
    side_effect: SideEffect
    confirmation_required: bool
    output_kind: str
    citation_policy: CitationPolicy
    fallback_policy: FallbackPolicy
    enabled: bool = True


PUBLIC_FIELDS = (
    "id",
    "name",
    "commands",
    "aliases",
    "description",
    "intent_examples",
    "exposure",
    "output_kind",
    "side_effect",
    "confirmation_required",
    "network_policy",
)


def import_reference(reference: str) -> Any:
    """Import a catalog reference in ``module:attribute`` form."""
    try:
        module_name, attribute = reference.split(":", 1)
    except ValueError as exc:
        raise ValueError(f"Invalid catalog reference: {reference}") from exc
    try:
        return getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError) as exc:
        raise ValueError(f"Unable to import catalog reference: {reference}") from exc


def public_projection(definition: SkillDefinition) -> dict[str, Any]:
    return {
        "id": definition.id,
        "name": definition.name,
        "commands": list(definition.commands),
        "aliases": list(definition.aliases),
        "description": definition.description,
        "intent_examples": list(definition.intent_examples),
        "exposure": definition.exposure,
        "output_kind": definition.output_kind,
        "side_effect": definition.side_effect,
        "confirmation_required": definition.confirmation_required,
        "network_policy": definition.network_policy,
    }


def enabled_agent_skills(definitions: list[SkillDefinition]) -> frozenset[str]:
    return frozenset(
        definition.agent_skill
        for definition in definitions
        if definition.enabled and definition.agent_skill
    )


def validate_service_bindings(
    definitions: list[SkillDefinition],
    services: dict[str, Any],
) -> None:
    for definition in definitions:
        if not definition.enabled:
            continue
        for service_name in definition.required_services:
            if service_name not in services:
                raise ValueError(
                    f"{definition.id} required service is missing: {service_name}"
                )


def validate_catalog(
    definitions: list[SkillDefinition],
    *,
    project_root: Path | None = None,
    validate_files: bool = True,
) -> None:
    seen_ids: set[str] = set()
    seen_commands: dict[str, str] = {}
    seen_aliases: dict[str, str] = {}
    root = project_root or Path(__file__).resolve().parents[2]

    for definition in definitions:
        if definition.id in seen_ids:
            raise ValueError(f"duplicate skill id: {definition.id}")
        seen_ids.add(definition.id)
        if not definition.commands and definition.exposure != "internal" and definition.handler:
            raise ValueError(f"{definition.id} has handler but no command")
        for command in definition.commands:
            if not command.startswith("/"):
                raise ValueError(f"{definition.id} command must start with '/': {command}")
            if command in seen_commands:
                raise ValueError(f"duplicate skill command: {command} ({seen_commands[command]} and {definition.id})")
            seen_commands[command] = definition.id
            if command in seen_aliases:
                raise ValueError(f"Command/alias collision: {command}")
        for alias in definition.aliases:
            if not alias.startswith("/"):
                raise ValueError(f"{definition.id} alias must start with '/': {alias}")
            if alias in seen_aliases:
                raise ValueError(f"duplicate skill alias: {alias} ({seen_aliases[alias]} and {definition.id})")
            if alias in seen_commands:
                raise ValueError(f"Command/alias collision: {alias}")
            seen_aliases[alias] = definition.id

        if definition.enabled and definition.handler and not definition.required_services:
            raise ValueError(f"{definition.id} handler must declare required_services")

        if definition.enabled and definition.handler:
            try:
                handler = import_reference(definition.handler)
            except ValueError as exc:
                raise ValueError(f"{definition.id} handler cannot be imported: {definition.handler}") from exc
            if not hasattr(handler, "run"):
                raise ValueError(f"{definition.id} handler does not implement run")
        if definition.enabled and definition.arguments_model:
            model = import_reference(definition.arguments_model)
            if not hasattr(model, "model_validate"):
                raise ValueError(f"{definition.id} arguments_model is not a Pydantic model")
            config = getattr(model, "model_config", {})
            if config.get("extra") != "forbid":
                raise ValueError(f"{definition.id} arguments_model must use extra='forbid'")
        if validate_files and definition.enabled and definition.agent_skill:
            skill_dir = root / ".claude" / "skills" / definition.agent_skill
            if not (skill_dir / "SKILL.md").is_file():
                raise ValueError(f"{definition.id} Agent Skill directory is missing: {definition.agent_skill}")

        if definition.side_effect != "read_only" and definition.handler and not definition.confirmation_required:
            raise ValueError(f"{definition.id} write Skill requires confirmation_required=True")
        if definition.exposure == "public" and definition.handler and not definition.enabled:
            continue
