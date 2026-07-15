from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LocalAgentSettings:
    enabled: bool = os.getenv("PNA_LOCAL_AGENT_ENABLED", "1") == "1"
    workspace_root: Path | None = (
        Path(os.environ["PNA_LOCAL_AGENT_WORKSPACE"]).expanduser()
        if os.getenv("PNA_LOCAL_AGENT_WORKSPACE")
        else None
    )
    provider_name: str = os.getenv("PNA_LOCAL_AGENT_PROVIDER", "openai-compatible-local")
    base_url: str = os.getenv("PNA_LOCAL_AGENT_BASE_URL", "http://127.0.0.1:11434/v1")
    chat_completions_path: str = os.getenv("PNA_LOCAL_AGENT_CHAT_PATH", "/chat/completions")
    default_model_key: str = os.getenv("PNA_LOCAL_AGENT_DEFAULT_MODEL", "yuanrong-personal-assistant")
    api_key: str | None = os.getenv("PNA_LOCAL_AGENT_API_KEY")
    timeout_seconds: float = float(os.getenv("PNA_LOCAL_AGENT_TIMEOUT_SECONDS", "120"))
    max_history_messages: int = int(os.getenv("PNA_LOCAL_AGENT_MAX_HISTORY", "30"))
    temperature: float = float(os.getenv("PNA_LOCAL_AGENT_TEMPERATURE", "0.2"))


settings = LocalAgentSettings()
