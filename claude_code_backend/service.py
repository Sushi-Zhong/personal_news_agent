from __future__ import annotations

from claude_code_backend.config import LocalAgentSettings, settings
from claude_code_backend.models import ChatMessage, ChatRequest, ChatResponse, SessionCreateRequest, SessionState, utc_now
from claude_code_backend.provider import LocalModelClient, LocalModelError, build_default_client
from claude_code_backend.session_store import InMemorySessionStore, SessionStore


class LocalAgentService:
    def __init__(
        self,
        store: SessionStore | None = None,
        client: LocalModelClient | None = None,
        config: LocalAgentSettings = settings,
    ) -> None:
        self.config = config
        self.store = store or InMemorySessionStore()
        self.client = client or build_default_client(config)

    def create_session(self, payload: SessionCreateRequest) -> SessionState:
        session = SessionState(
            user_id=payload.user_id,
            title=payload.title,
            project_context=payload.project_context,
            metadata=payload.metadata,
        )
        self.store.save(session)
        return session

    def get_session(self, session_id: str) -> SessionState | None:
        return self.store.get(session_id)

    async def chat(self, payload: ChatRequest) -> ChatResponse:
        session = self._resolve_session(payload)
        user_message = ChatMessage(
            role="user",
            content=payload.message,
            metadata={
                "attachments": [attachment.model_dump(mode="json") for attachment in payload.attachments],
                "request_metadata": payload.metadata,
            },
        )
        session.messages.append(user_message)
        session.project_context.update(payload.project_context)
        session.updated_at = utc_now()

        history = session.messages[-self.config.max_history_messages :]
        try:
            text, provider_metadata = await self.client.complete(payload, history)
            status = "ok"
        except LocalModelError as exc:
            text = str(exc)
            provider_metadata = {
                "provider": self.config.provider_name,
                "model_key": payload.model_key or self.config.default_model_key,
                "base_url": self.config.base_url,
            }
            status = "error"

        assistant_message = ChatMessage(role="assistant", content=text, metadata={"provider": provider_metadata})
        session.messages.append(assistant_message)
        session.updated_at = utc_now()
        self.store.save(session)

        return ChatResponse(
            session_id=session.session_id,
            message=assistant_message,
            status=status,
            provider_metadata=provider_metadata,
        )

    def _resolve_session(self, payload: ChatRequest) -> SessionState:
        if payload.session_id:
            session = self.store.get(payload.session_id)
            if session:
                return session
        session = SessionState(user_id=payload.user_id, project_context=payload.project_context)
        self.store.save(session)
        return session
