from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


Role = Literal["system", "user", "assistant", "tool"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Attachment(BaseModel):
    name: str
    content_type: str | None = None
    content: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatMessage(BaseModel):
    role: Role
    content: str
    created_at: datetime = Field(default_factory=utc_now)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionCreateRequest(BaseModel):
    user_id: str = "default"
    title: str | None = None
    project_context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SessionState(BaseModel):
    session_id: str = Field(default_factory=lambda: uuid4().hex)
    user_id: str = "default"
    title: str | None = None
    project_context: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    messages: list[ChatMessage] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatRequest(BaseModel):
    session_id: str | None = None
    user_id: str = "default"
    model_key: str | None = None
    message: str = Field(min_length=1)
    project_context: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    message: ChatMessage
    status: Literal["ok", "error"] = "ok"
    usage: dict[str, Any] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: Literal["ok"]
    enabled: bool
    provider: str
    default_model: str
    base_url: str
    model_options: list[dict[str, Any]]
    routes: list[str]
