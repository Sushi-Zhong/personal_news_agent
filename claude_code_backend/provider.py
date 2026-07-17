from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Protocol
from uuid import UUID, NAMESPACE_URL, uuid5

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


class ClaudeCodeCliClient:
    """Run Claude Code in non-interactive mode without shell interpolation."""

    def __init__(self, config: LocalAgentSettings = settings) -> None:
        self.config = config

    async def complete(self, request: ChatRequest, history: list[ChatMessage]) -> tuple[str, dict]:
        workspace = (self.config.workspace_root or Path.cwd()).resolve()
        if not workspace.is_dir():
            raise LocalModelError(f"Claude Code workspace does not exist: {workspace}")

        session_id = self._claude_session_id(request.session_id)
        has_previous_answer = any(message.role == "assistant" for message in history[:-1])
        prompt = self._build_prompt(request)
        command = [
            self.config.claude_binary,
            "-p",
            prompt,
            "--output-format",
            "json",
            "--permission-mode",
            self.config.claude_permission_mode,
            "--max-turns",
            str(self.config.claude_max_turns),
        ]
        if session_id:
            command.extend(["--resume" if has_previous_answer else "--session-id", session_id])
        if self.config.claude_model:
            command.extend(["--model", self.config.claude_model])
        if self.config.claude_max_budget_usd is not None:
            command.extend(["--max-budget-usd", str(self.config.claude_max_budget_usd)])

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise LocalModelError(
                f"Claude Code executable was not found: {self.config.claude_binary}"
            ) from exc

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.config.timeout_seconds
            )
        except TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise LocalModelError("Claude Code request timed out") from exc

        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"exit code {process.returncode}"
            raise LocalModelError(f"Claude Code request failed: {detail}")

        try:
            data = json.loads(stdout_text)
        except json.JSONDecodeError as exc:
            raise LocalModelError("Claude Code did not return valid JSON") from exc
        content = data.get("result") or data.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LocalModelError("Claude Code JSON response did not contain result text")

        return content.strip(), {
            "provider": "claude-code-cli",
            "session_id": data.get("session_id") or session_id,
            "model": self.config.claude_model,
            "usage": data.get("usage") or {},
            "total_cost_usd": data.get("total_cost_usd"),
            "duration_ms": data.get("duration_ms"),
            "permission_mode": self.config.claude_permission_mode,
            "workspace": str(workspace),
        }

    @staticmethod
    def _build_prompt(request: ChatRequest) -> str:
        if not request.project_context:
            return request.message
        context = json.dumps(request.project_context, ensure_ascii=False, default=str)
        return f"Project context:\n{context}\n\nUser request:\n{request.message}"

    @staticmethod
    def _claude_session_id(value: str | None) -> str | None:
        if not value:
            return None
        try:
            return str(UUID(value))
        except ValueError:
            return str(uuid5(NAMESPACE_URL, value))


def build_default_client(config: LocalAgentSettings = settings) -> LocalModelClient:
    if config.provider_name == "claude-code-cli":
        return ClaudeCodeCliClient(config)
    return OpenAICompatibleLocalModelClient(config)
