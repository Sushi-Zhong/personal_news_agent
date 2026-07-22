from __future__ import annotations

import json
import httpx

from personal_news_agent.config import Settings, settings
from personal_news_agent.services.model_config import get_model_option


class LLMClient:
    def __init__(self, app_settings: Settings = settings):
        self.settings = app_settings

    @property
    def configured(self) -> bool:
        return bool(self.settings.llm_endpoint and self.settings.llm_key)

    async def chat(self, messages: list[dict[str, str]], model_key: str | None = None) -> str:
        return await self._complete(messages, model_key=model_key)

    async def structured(self, messages: list[dict[str, str]], schema_name: str, schema: dict, model_key: str | None = None) -> dict:
        try:
            content = await self._complete(
                messages,
                model_key=model_key,
                response_format={"type": "json_schema", "json_schema": {"name": schema_name, "strict": True, "schema": schema}},
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code not in {400, 422}:
                raise
            content = await self._complete(messages, model_key=model_key, response_format={"type": "json_object"})
        return json.loads(content)

    async def _complete(self, messages: list[dict[str, str]], model_key: str | None = None, response_format: dict | None = None) -> str:
        if not self.configured:
            raise RuntimeError("LLM endpoint/key is not configured")
        model = get_model_option(model_key, self.settings)
        url = self.settings.llm_endpoint.rstrip("/") + "/chat/completions"
        payload = {
            "model": model.provider_model,
            "messages": messages,
            "temperature": 0.2,
            "stream": False,
        }
        if response_format:
            payload["response_format"] = response_format
        headers = {"Authorization": f"Bearer {self.settings.llm_key}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, headers=headers) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("LLM response has no choices")
        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            raise RuntimeError("LLM response is empty")
        return content.strip()
