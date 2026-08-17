from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from personal_news_agent.core.models import EvidenceRef


class ChangeDigestData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    baseline_label: str = ""
    baseline_at: datetime | None = None
    change_status: Literal["changed", "no_material_change", "insufficient"]
    new_facts: list[dict[str, Any]] = Field(default_factory=list)
    status_changes: list[dict[str, Any]] = Field(default_factory=list)
    number_changes: list[dict[str, Any]] = Field(default_factory=list)
    corrections: list[dict[str, Any]] = Field(default_factory=list)
    repeated_reports: list[dict[str, Any]] = Field(default_factory=list)
    watch_next: list[dict[str, Any] | str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)


class CoverageCompareData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    comparison_status: Literal["sufficient", "insufficient"]
    common_facts: list[dict[str, Any]] = Field(default_factory=list)
    unique_claims: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    framing_differences: list[dict[str, Any]] = Field(default_factory=list)
    missing_questions: list[str] = Field(default_factory=list)
    source_groups: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
