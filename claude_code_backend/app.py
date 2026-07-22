from __future__ import annotations

from fastapi import FastAPI

from claude_code_backend.routes import create_local_agent_router


def create_app() -> FastAPI:
    app = FastAPI(title="Claude Code / Local Agent Backend")
    app.include_router(create_local_agent_router())
    return app


app = create_app()
