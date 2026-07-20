from __future__ import annotations

import shlex

from personal_news_agent.skills.base import Skill, SkillContext, SkillResult, SkillSpec
from personal_news_agent.skills.brief import BriefSkill
from personal_news_agent.skills.factcheck import CheckSkill, FactCheckSkill
from personal_news_agent.skills.report import ReportSkill
from personal_news_agent.skills.sources import SourcesSkill


class SkillRegistry:
    def __init__(self, skills: list[Skill] | None = None):
        self._skills: dict[str, Skill] = {}
        for skill in skills or []:
            self.register(skill)

    def register(self, skill: Skill) -> None:
        command = skill.spec.command.lower()
        if not command.startswith("/"):
            raise ValueError("Skill command must start with '/'")
        if command in self._skills:
            raise ValueError(f"Duplicate skill command: {command}")
        self._skills[command] = skill

    def list_skills(self) -> list[SkillSpec]:
        return [skill.spec for skill in self._skills.values()]

    def help_text(self) -> str:
        return "\n".join(
            f"{spec.command:<10} {spec.description} 用法：{spec.usage}"
            for spec in self.list_skills()
        )

    async def execute(self, text: str, context: SkillContext) -> SkillResult:
        parts = shlex.split(text.strip())
        if not parts:
            raise ValueError("Skill command is empty")
        command = parts[0].lower()
        skill = self._skills.get(command)
        if not skill:
            raise ValueError(f"未知 Skill：{command}")
        return await skill.run(parts[1:], context)


def build_default_registry() -> SkillRegistry:
    return SkillRegistry([ReportSkill(), BriefSkill(), FactCheckSkill(), CheckSkill(), SourcesSkill()])
