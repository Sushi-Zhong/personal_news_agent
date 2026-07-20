from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class SkillSpec:
    command: str
    name: str
    description: str
    usage: str
    examples: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillContext:
    services: dict[str, Any]
    user_id: str = "default"
    conversation_id: str | None = None


@dataclass(frozen=True)
class SkillResult:
    command: str
    title: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


class Skill(Protocol):
    spec: SkillSpec

    async def run(self, args: list[str], context: SkillContext) -> SkillResult: ...
