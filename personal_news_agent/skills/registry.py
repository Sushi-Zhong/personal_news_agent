from __future__ import annotations

import shlex
from dataclasses import replace
from typing import Any

from pydantic import ValidationError

from personal_news_agent.services.evidence import EvidenceLedger
from personal_news_agent.skills.base import Skill, SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.catalog import all_definitions
from personal_news_agent.skills.manifest import SkillDefinition, import_reference


class SkillRegistry:
    def __init__(
        self,
        skills: list[Skill] | None = None,
        *,
        definitions: list[SkillDefinition] | None = None,
    ):
        self._skills: dict[str, Skill] = {}
        self._skills_by_id: dict[str, Skill] = {}
        self._definitions: dict[str, SkillDefinition] = {}
        self._definitions_by_command: dict[str, SkillDefinition] = {}
        definitions_by_command = {
            definition.commands[0]: definition
            for definition in definitions or []
            if definition.commands
        }
        for skill in skills or []:
            self.register(skill, definitions_by_command.get(skill.spec.command))

    def register(self, skill: Skill, definition: SkillDefinition | None = None) -> None:
        command = skill.spec.command.lower()
        if not command.startswith("/"):
            raise ValueError("Skill command must start with '/'")
        if command in self._skills:
            raise ValueError(f"Duplicate skill command: {command}")
        definition = definition or _legacy_definition(skill.spec)
        canonical = definition.commands[0].lower() if definition.commands else command
        if canonical != command:
            raise ValueError(f"Skill command mismatch for {definition.id}: {command} != {canonical}")
        self._skills[command] = skill
        self._skills_by_id[definition.id] = skill
        self._definitions[definition.id] = definition
        self._definitions_by_command[command] = definition
        for alias in definition.aliases:
            key = alias.lower()
            if key in self._skills:
                raise ValueError(f"Duplicate skill command or alias: {key}")
            self._skills[key] = skill
            self._definitions_by_command[key] = definition

    def list_skills(self) -> list[SkillSpec]:
        return [self._skills_by_id[skill_id].spec for skill_id in self._definitions]

    def help_text(self) -> str:
        return "\n".join(
            f"{spec.command:<10} {spec.description} 用法：{spec.usage}"
            for spec in self.list_skills()
        )

    async def execute_text(self, text: str, context: SkillContext) -> SkillResult:
        parts = shlex.split(text.strip())
        if not parts:
            raise ValueError("Skill command is empty")
        command = parts[0].lower()
        skill = self._skills.get(command)
        if not skill:
            raise ValueError(f"未知 Skill：{command}")
        definition = self._definitions_by_command[command]
        result = await skill.run(parts[1:], _effective_context(context, definition))
        return _apply_definition(result, definition)

    async def execute_structured(
        self,
        skill_id: str,
        arguments: dict[str, Any],
        context: SkillContext,
    ) -> SkillResult:
        definition = self._definitions.get(skill_id)
        skill = self._skills_by_id.get(skill_id)
        if not definition or not skill:
            raise ValueError(f"未知 Skill：{skill_id}")
        if not definition.arguments_model:
            raise ValueError(f"{skill_id} structured arguments are not supported")
        model_type = import_reference(definition.arguments_model)
        try:
            parsed = model_type.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"{skill_id} arguments are invalid: {exc}") from exc
        run_structured = getattr(skill, "run_structured", None)
        effective_context = _effective_context(context, definition)
        if callable(run_structured):
            result = await run_structured(parsed, effective_context)
            return _apply_definition(result, definition)
        adapter = getattr(parsed, "to_legacy_argv", None)
        if not callable(adapter):
            raise ValueError(f"{skill_id} arguments adapter is missing")
        result = await skill.run(adapter(), effective_context)
        return _apply_definition(result, definition)

    async def execute(self, text: str, context: SkillContext) -> SkillResult:
        return await self.execute_text(text, context)


def build_default_registry() -> SkillRegistry:
    definitions = [
        definition
        for definition in all_definitions()
        if definition.enabled and definition.handler and definition.commands
    ]
    skills = [import_reference(definition.handler)() for definition in definitions if definition.handler]
    return SkillRegistry(skills, definitions=definitions)


def _legacy_definition(spec: SkillSpec) -> SkillDefinition:
    return SkillDefinition(
        id=spec.command.removeprefix("/").replace("-", "_"),
        name=spec.name,
        commands=(spec.command,),
        aliases=(),
        exposure="public",
        description=spec.description,
        intent_examples=spec.examples,
        handler=None,
        arguments_model=None,
        agent_skill=None,
        required_services=(),
        network_policy="local_only",
        side_effect="read_only",
        confirmation_required=False,
        output_kind="default_markdown",
        citation_policy="none",
        fallback_policy="default_markdown",
    )


def _apply_definition(result: SkillResult, definition: SkillDefinition) -> SkillResult:
    evidence = result.evidence or _project_result_evidence(result.data, definition)
    return replace(
        result,
        skill_id=definition.id,
        output_kind=definition.output_kind,
        evidence=evidence,
    )


def _effective_context(
    context: SkillContext,
    definition: SkillDefinition,
) -> SkillContext:
    if definition.network_policy == "local_only" and context.allow_web_search:
        return replace(context, allow_web_search=False)
    return context


def _project_result_evidence(
    data: dict[str, Any],
    definition: SkillDefinition,
) -> tuple[Any, ...]:
    if definition.citation_policy == "none":
        return ()
    ledger = EvidenceLedger()
    for field in ("evidence", "sources"):
        candidates = data.get(field)
        if not isinstance(candidates, list):
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            role = candidate.get("claim_role")
            if role not in {"supporting", "contradicting", "context", "baseline", "current", "repeated"}:
                role = "context"
            try:
                ledger.add(candidate, claim_role=role)
            except (TypeError, ValueError, ValidationError):
                continue
    return ledger.references
