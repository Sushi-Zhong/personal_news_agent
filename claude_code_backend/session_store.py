from __future__ import annotations

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
