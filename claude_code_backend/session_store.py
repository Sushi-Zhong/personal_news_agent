from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from claude_code_backend.models import SessionState


class SessionStore(Protocol):
    def get(self, session_id: str) -> SessionState | None:
        ...

    def save(self, session: SessionState) -> None:
        ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def save(self, session: SessionState) -> None:
        self._sessions[session.session_id] = session


class JsonFileSessionStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._sessions: dict[str, SessionState] = {}
        self._loaded = False

    def get(self, session_id: str) -> SessionState | None:
        self._ensure_loaded()
        return self._sessions.get(session_id)

    def save(self, session: SessionState) -> None:
        self._ensure_loaded()
        self._sessions[session.session_id] = session
        self._flush()

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if self.path.exists():
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._sessions = {
                session_id: SessionState.model_validate(payload)
                for session_id, payload in raw.get("sessions", {}).items()
            }
        self._loaded = True

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "sessions": {
                session_id: session.model_dump(mode="json")
                for session_id, session in self._sessions.items()
            }
        }
        tmp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)
