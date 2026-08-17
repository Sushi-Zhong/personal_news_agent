from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from personal_news_agent.core.models import EvidenceRef


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
    topic: str | None = None
    category_scope: list[str] | None = None
    allow_web_search: bool = False
    on_trace: Callable[[dict[str, Any]], Awaitable[None]] | None = None


@dataclass(frozen=True)
class SkillResult:
    command: str
    title: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    skill_id: str = ""
    status: Literal["success", "degraded", "blocked", "failed"] = "success"
    output_kind: str = "default_markdown"
    evidence: tuple[EvidenceRef, ...] = ()
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.skill_id:
            object.__setattr__(self, "skill_id", self.command.removeprefix("/").replace("-", "_"))


class Skill(Protocol):
    spec: SkillSpec

    async def run(self, args: list[str], context: SkillContext) -> SkillResult: ...
