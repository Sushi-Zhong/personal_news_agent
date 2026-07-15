# Local Agent SDK Backend

This directory is an isolated backend scaffold for a Claude Agent SDK-style
coding agent that runs against a local model service.

It does not include a Claude-specific model entry and does not call Anthropic by
default. The model backend is expected to be a local OpenAI-compatible server,
such as Ollama, LM Studio, vLLM, llama.cpp server, or another local gateway.

## What is included

- `models.py`: request and response schemas for sessions, messages, attachments,
  and chat responses.
- `session_store.py`: in-memory session storage that can later be replaced by
  SQLite, MySQL, Redis, or the project's existing storage layer.
- `service.py`: local-agent orchestration for session history and provider calls.
- `provider.py`: OpenAI-compatible local model client.
- `model_config.py`: NPA-style model options and `model_key` to `provider_model`
  mapping.
- `routes.py`: FastAPI router under `/api/local-agent`.
- `config.py`: environment-based local model settings.
- `app.py`: optional standalone FastAPI app for local smoke testing.

## Default local model contract

The provider calls:

```text
POST http://127.0.0.1:11434/v1/chat/completions
```

with an OpenAI-compatible payload:

```json
{
  "model": "qwen3.5-plus",
  "messages": [],
  "temperature": 0.2,
  "stream": false
}
```

This follows NPA's existing model contract: the API receives a `model_key`, then
resolves it to the provider-facing `provider_model`.

## Model options

The local agent currently mirrors the NPA model configuration:

```text
yuanrong-personal-assistant -> qwen3.5-plus
qwen3.5-flash              -> qwen3.5-flash
qwen3.5-plus               -> qwen3.5-plus
deepseek-v4-flash          -> deepseek-v4-flash
```

`yuanrong-personal-assistant` also injects the same fixed system prompt used by
NPA:

```text
你是元融个人助理大模型，回答要简洁、可信、贴合用户长期兴趣。
```

Your local model server must expose these model names, or route them internally
to locally installed models.

## Environment variables

```bash
PNA_LOCAL_AGENT_ENABLED=1
PNA_LOCAL_AGENT_PROVIDER=openai-compatible-local
PNA_LOCAL_AGENT_BASE_URL=http://127.0.0.1:11434/v1
PNA_LOCAL_AGENT_CHAT_PATH=/chat/completions
PNA_LOCAL_AGENT_DEFAULT_MODEL=yuanrong-personal-assistant
PNA_LOCAL_AGENT_API_KEY=
PNA_LOCAL_AGENT_TIMEOUT_SECONDS=120
PNA_LOCAL_AGENT_MAX_HISTORY=30
PNA_LOCAL_AGENT_TEMPERATURE=0.2
```

## Optional integration later

When you are ready to connect it to the existing FastAPI app, add this to the
main app setup:

```python
from claude_code_backend import create_local_agent_router

app.include_router(create_local_agent_router())
```

The scaffold intentionally avoids changing the existing app for now.

## Standalone local run

Start a local model first. For Ollama:

```bash
ollama serve
```

Make sure your local server can serve or alias `qwen3.5-plus`,
`qwen3.5-flash`, and `deepseek-v4-flash` if you want to use the default model
keys unchanged.

Then run this backend:

```bash
uvicorn claude_code_backend.app:app --reload
```

Smoke test:

```bash
curl -X POST http://127.0.0.1:8000/api/local-agent/chat \
  -H "Content-Type: application/json" \
  -d '{"model_key":"yuanrong-personal-assistant","message":"hello","project_context":{"feature":"personal news"}}'
```

Model list:

```bash
curl http://127.0.0.1:8000/api/local-agent/models
```

## Next steps

The current provider only sends chat messages to a local model. To become a full
Claude Agent SDK-style coding agent, add a tool layer behind the service:

- read files
- write or patch files
- run shell commands with permission checks
- stream tool events to the frontend
- persist sessions outside memory
- add workspace allowlists and command policies
