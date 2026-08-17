from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ResearchSkillArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None, max_length=240)
    category_scope: list[str] = Field(default_factory=list, max_length=8)
    time_range: str | None = Field(default=None, max_length=40)

    def to_legacy_argv(self) -> list[str]:
        argv = [self.topic] if self.topic else []
        if self.category_scope:
            argv.extend(["--category", ",".join(self.category_scope)])
        if self.time_range:
            argv.extend(["--time-range", self.time_range])
        return argv


class ChangedArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None, max_length=240)
    baseline_expression: str | None = Field(default=None, max_length=80)
    category_scope: list[str] = Field(default_factory=list, max_length=8)

    def to_legacy_argv(self) -> list[str]:
        argv = [self.topic] if self.topic else []
        if self.baseline_expression:
            argv.extend(["--baseline", self.baseline_expression])
        if self.category_scope:
            argv.extend(["--category", ",".join(self.category_scope)])
        return argv


class CompareArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None, max_length=240)
    category_scope: list[str] = Field(default_factory=list, max_length=8)
    source_scope: list[str] = Field(default_factory=list, max_length=16)

    def to_legacy_argv(self) -> list[str]:
        argv = [self.topic] if self.topic else []
        if self.category_scope:
            argv.extend(["--category", ",".join(self.category_scope)])
        if self.source_scope:
            argv.extend(["--source", ",".join(self.source_scope)])
        return argv


class ScheduleArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_request: str = Field(min_length=1, max_length=2000)

    def to_legacy_argv(self) -> list[str]:
        return [self.raw_request]
