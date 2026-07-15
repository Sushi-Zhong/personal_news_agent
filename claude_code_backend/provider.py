from __future__ import annotations

from typing import Protocol

import httpx

from claude_code_backend.config import LocalAgentSettings, settings
from claude_code_backend.models import ChatMessage, ChatRequest
from claude_code_backend.model_config import get_model_option


class LocalModelClient(Protocol):
    async def complete(self, request: ChatRequest, history: list[ChatMessage]) -> tuple[str, dict]:
        """Return assistant text and provider metadata."""


class LocalModelError(RuntimeError):
    pass


class OpenAICompatibleLocalModelClient:
    def __init__(self, config: LocalAgentSettings = settings) -> None:
        self.config = config

    async def complete(self, request: ChatRequest, history: list[ChatMessage]) -> tuple[str, dict]:
        model = get_model_option(request.model_key, self.config)
        messages = self._build_messages(request, history, model.fixed_system_prompt)
        url = self.config.base_url.rstrip("/") + self.config.chat_completions_path
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        payload = {
            "model": model.provider_model,
            "messages": messages,
            "temperature": self.config.temperature,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LocalModelError(f"local model request failed: {exc}") from exc

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LocalModelError("local model response did not match OpenAI-compatible chat format") from exc

        return content, {
            "provider": self.config.provider_name,
            "model_key": model.key,
            "provider_model": model.provider_model,
            "base_url": self.config.base_url,
            "usage": data.get("usage", {}),
            "history_messages": len(history),
        }

    def _build_messages(
        self,
        request: ChatRequest,
        history: list[ChatMessage],
        fixed_system_prompt: str = "",
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        if fixed_system_prompt:
            messages.append({"role": "system", "content": fixed_system_prompt})
        if request.project_context:
            messages.append(
                {
                    "role": "system",
                    "content": (
                        "You are a local coding agent running inside the user's project backend. "
                        "Use the provided project context, keep actions explicit, and do not assume cloud services."
                    ),
                }
            )
            messages.append({"role": "system", "content": f"Project context: {request.project_context}"})
        for item in history:
            if item.role in {"system", "user", "assistant"}:
                messages.append({"role": item.role, "content": item.content})
        return messages


def build_default_client(config: LocalAgentSettings = settings) -> LocalModelClient:
    return OpenAICompatibleLocalModelClient(config)
