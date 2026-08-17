# Skill System Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved Skill Manifest/Router architecture, unified result and evidence contracts, safe natural-language schedule confirmation, `/changed`, `/compare`, and the compatible Web/mobile presentation paths without database migration or VCS finalization.

**Architecture:** A typed Python catalog is the single declaration source for Registry commands, CC Runtime skill allow-list entries, public `/api/skills` projections, and frontend slash-menu metadata. `SkillRouter` performs slash/rule/model selection only; `SkillRegistry.execute_text()` and `execute_structured()` converge on the same validated argument model, handler, and business Service. Read-only Skills execute directly, while natural-language schedule produces a frozen preview and process-local one-time confirmation token before the existing task Service creates anything.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, pytest/pytest-asyncio, existing `LLMClient.structured()`, existing Claude Agent SDK runtime, SQLite-backed `NewsStore` (no schema change), and the existing Web/Mobile static JavaScript renderer.

---

## Execution Constraints

- Work only in `/Users/zdr/personal_news_agent` and preserve all pre-existing dirty-worktree changes.
- Do not run `git reset`, `git checkout`, or other destructive commands.
- Do not add a database migration, new model provider, multi-Skill orchestration, management UI, commit, push, or PR.
- Every implementation task follows failing test -> observed failure -> minimal implementation -> focused passing test -> stage regression.
- The current official OpenAI documentation MCP is not exposed (`list_mcp_resources` and `list_mcp_resource_templates` are empty). Do not invent an API; retain the local `LLMClient.structured(messages, schema_name, schema, model_key)` contract and record this limitation in the delivery report.
- Explicit legacy `/schedule` remains immediate for compatibility. Only natural-language schedule routing is gated by confirmation.
- V1 confirmation state is process-local and therefore valid only for the current single API process; do not claim multi-worker consistency.

## File Map

### New files

- `personal_news_agent/skills/manifest.py`: frozen `SkillDefinition`, exposure/network/side-effect literals, catalog validation, dynamic handler/argument-model loading.
- `personal_news_agent/skills/catalog.py`: canonical declarations for seven existing Registry Skills, `changed`, `compare`, the internal research Agent workflow, context actions, and Agent Skill names.
- `personal_news_agent/skills/result_models.py`: `EvidenceRef`, `ChangeDigestData`, `CoverageCompareData`, small schedule confirmation/preview models, and Pydantic validators for per-Skill `data`.
- `personal_news_agent/services/evidence.py`: per-run evidence ledger, URL/index validation, and `EvidenceRef` projection.
- `personal_news_agent/services/skill_router.py`: `SkillRoute`, deterministic rules, optional model classification, confidence thresholds, and the single-Skill route contract.
- `personal_news_agent/services/time_context.py`: application `ZoneInfo`, `PNA_APP_TIMEZONE` validation, relative-time parsing helpers, and display conversion.
- `personal_news_agent/services/schedule_confirmation.py`: 15-minute in-memory token registry, binding checks, digest checks, state machine, idempotent confirmation/cancellation, and replay tombstones.
- `personal_news_agent/skills/changed.py`: thin `/changed` handler and structured/legacy argument adapters.
- `personal_news_agent/services/change_detection.py`: baseline selection, material-change classification, duplicate/repost treatment, Agent fallback, and result validation.
- `personal_news_agent/skills/compare.py`: thin `/compare` handler and structured/legacy argument adapters.
- `personal_news_agent/services/coverage_comparison.py`: source grouping, shared/unique/conflicting/framing claim classification, Agent fallback, and result validation.
- `.claude/skills/news-change-digest/SKILL.md` and `agents/openai.yaml`: constrained change-digest Agent Skill.
- `.claude/skills/news-coverage-compare/SKILL.md` and `agents/openai.yaml`: constrained source-coverage comparison Agent Skill.
- `tests/test_skill_manifest.py`, `tests/test_skill_router.py`, `tests/test_result_contracts.py`, `tests/test_schedule_confirmation.py`, `tests/test_timezone_policy.py`, `tests/test_change_detection.py`, `tests/test_coverage_comparison.py`: focused unit contracts.

### Modified files

- `personal_news_agent/skills/base.py`, `skills/registry.py`, `skills/__init__.py`: compatibility execution methods, manifest-backed registration, and result envelope defaults.
- Existing Skill handlers (`brief.py`, `factcheck.py`, `hot_event_map.py`, `related.py`, `report.py`, `schedule.py`, `sources.py`): manifest/argument adapters, unified envelope, and factcheck web-policy propagation.
- `personal_news_agent/core/models.py`: additive `ChatResponse` fields and `EvidenceRef` compatibility where the existing response model is the public envelope.
- `personal_news_agent/api/schemas.py`, `api/routes.py`: `/api/skills`, schedule preview/confirm/cancel action schemas and routes, with existing routes preserved.
- `personal_news_agent/services/factory.py`, `services/chat.py`, `services/cc_runtime.py`, `services/factcheck.py`, `services/tasks.py`, `config.py`: dependency wiring, shared sync/SSE route execution, derived allow-list, offline factcheck gate, application timezone, preview split, and removal of duplicate chat schedule path.
- `.env.example`: `PNA_APP_TIMEZONE=Asia/Shanghai` documentation.
- `personal_news_agent/static/shared.js`, `static/web.js`, `static/mobile.js`, `static/home.html`, `static/mobile.html`: API-driven public menu, output renderer map, schedule confirmation/change/compare cards, and safe API-failure behavior.
- `personal_news_agent/skills/README.md`, `README.md`, and existing focused tests: catalog count/roles and compatibility expectations.

## Stage 1: Manifest, Registry, and Runtime Allow-List

### Task 1: Add typed Manifest and canonical catalog

**Files:**
- Create: `personal_news_agent/skills/manifest.py`
- Create: `personal_news_agent/skills/catalog.py`
- Test: `tests/test_skill_manifest.py`

- [ ] **Step 1: Write failing catalog validation tests.**

  Cover: duplicate canonical commands, duplicate aliases, alias/command collision, missing handler import, missing required service declaration, invalid `agent_skill` directory, routable Skill without `arguments_model`, and `enabled=False` exclusion. Assert the error names the Skill ID and conflicting value.

- [ ] **Step 2: Run the focused test and observe the expected import/validation failure.**

  Run: `.venv/bin/pytest -q tests/test_skill_manifest.py`

  Expected: collection or assertion failure because `manifest.py` and `catalog.py` do not exist yet.

- [ ] **Step 3: Implement the manifest types and validators.**

  Define the approved fields exactly:

  ```python
  @dataclass(frozen=True)
  class SkillDefinition:
      id: str
      name: str
      commands: tuple[str, ...]
      aliases: tuple[str, ...]
      exposure: Literal["public", "internal", "admin", "context_action"]
      description: str
      intent_examples: tuple[str, ...]
      handler: str | None
      arguments_model: str | None
      agent_skill: str | None
      required_services: tuple[str, ...]
      network_policy: Literal["local_only", "user_controlled"]
      side_effect: Literal["read_only", "creates_task", "updates_state", "admin_write"]
      confirmation_required: bool
      output_kind: str
      citation_policy: Literal["none", "evidence_if_claims", "evidence_required"]
      fallback_policy: Literal["local_service", "local_evidence_only", "default_markdown", "blocked"]
      enabled: bool = True
  ```

  Add `all_definitions()`, `validate_catalog()`, command/alias indexes, import validation, `.claude/skills/<agent_skill>/SKILL.md` validation, and public projection that omits handler, Agent, service, fallback, and internal configuration fields.

- [ ] **Step 4: Declare the catalog without changing existing Registry behavior.**

  Declare `/report`, `/brief`, `/factcheck`, `/map`, `/related`, `/sources`, `/schedule`, `changed`, and `compare`; classify `related=context_action`, `sources=admin`, `news-conversation-research=internal`, and schedule as `public + creates_task + confirmation_required`. Keep old commands and aliases (`/r`, `/verify`, `/graph`) in the catalog. Keep UI context actions separate from Python handlers.

- [ ] **Step 5: Run the focused manifest tests and inspect the catalog projection.**

  Run: `.venv/bin/pytest -q tests/test_skill_manifest.py`

  Expected: all validation/projection tests pass and only the document-approved public fields are exposed.

### Task 2: Migrate Registry with text/structured compatibility

**Files:**
- Modify: `personal_news_agent/skills/base.py`
- Modify: `personal_news_agent/skills/registry.py`
- Modify: `personal_news_agent/skills/__init__.py`
- Modify: existing Skill handler files as needed for `handler` references
- Test: `tests/test_registry.py`, `tests/test_skill_manifest.py`

- [ ] **Step 1: Add failing tests for the two execution interfaces.**

  Assert the following signatures and behavior:

  ```python
  await registry.execute_text("/brief AI", context)
  await registry.execute_structured("changed", {"topic": "AI"}, context)
  ```

  `execute_text()` must retain `shlex.split`, command/alias lookup, and legacy unknown-command errors. `execute_structured()` must validate the catalog `arguments_model`, reject extra fields, and call `run_structured(arguments, context)` when available; otherwise serialize with a deterministic `to_legacy_argv(model)` adapter and call the existing `run(list[str], context)`.

- [ ] **Step 2: Run `tests/test_registry.py` and the new protocol tests to confirm they fail.**

  Run: `.venv/bin/pytest -q tests/test_registry.py tests/test_skill_manifest.py -k 'execute or structured or alias'`

  Expected: missing methods or assertion failures against the current single `execute(text, context)` implementation.

- [ ] **Step 3: Implement compatibility methods and catalog-backed indexes.**

  Keep `execute(text, context)` for one compatibility cycle as a direct delegate to `execute_text`. Do not rebuild structured arguments into a shell string. Add protocol checks so both entry points converge on the same handler/Service and preserve `SkillResult` compatibility.

- [ ] **Step 4: Run focused registry and existing skill tests.**

  Run: `.venv/bin/pytest -q tests/test_registry.py tests/test_services.py -k 'skill or schedule'`

  Expected: new interface tests and existing legacy Skill tests pass.

## Stage 2: Result/Evidence Envelope and Public Skill API

### Task 3: Add additive result and evidence contracts

**Files:**
- Create: `personal_news_agent/services/evidence.py`
- Create/modify: `personal_news_agent/skills/result_models.py`
- Modify: `personal_news_agent/skills/base.py`
- Modify: `personal_news_agent/core/models.py`
- Modify: existing Skill handlers that construct results
- Test: `tests/test_result_contracts.py`, selected `tests/test_services.py`

- [ ] **Step 1: Write failing envelope and evidence tests.**

  Assert top-level execution fields are `status` (`success|degraded|blocked|failed`), `output_kind`, `evidence`, and `fallback_reason`; changed/compare business models use only `change_status`/`comparison_status`. Assert `EvidenceRef` serializes `index`, `title`, `url`, `source_id`, `published_at`, `origin`, and `claim_role`, and rejects references not present in the current ledger.

- [ ] **Step 2: Run the focused tests and observe missing fields/models.**

  Run: `.venv/bin/pytest -q tests/test_result_contracts.py`

  Expected: failure because the current `SkillResult`/`ChatResponse` lack the additive envelope and the evidence payloads are unvalidated dictionaries.

- [ ] **Step 3: Implement small models and preserve legacy fields.**

  Add defaults so old stored responses remain readable: missing envelope fields deserialize as `success`, `default_markdown`, and an empty evidence list. Keep all existing `command/title/message/data`, Markdown, report-download, and factcheck fields. Implement an `EvidenceLedger` that assigns stable per-run indices and filters model claims by index and canonical URL.

- [ ] **Step 4: Run the result-contract tests and existing response tests.**

  Run: `.venv/bin/pytest -q tests/test_result_contracts.py tests/test_api.py -k 'chat or factcheck or report'`

  Expected: additive schema tests pass and old ChatResponse API behavior remains green.

### Task 4: Expose `/api/skills` and derive CC Runtime allow-list

**Files:**
- Modify: `personal_news_agent/api/routes.py`
- Modify: `personal_news_agent/services/cc_runtime.py`
- Modify: `personal_news_agent/services/factory.py`
- Modify: `personal_news_agent/static/shared.js`
- Modify: `personal_news_agent/static/web.js`
- Modify: `personal_news_agent/static/mobile.js`
- Test: `tests/test_api.py`, `tests/test_subpath_frontend.py`, `tests/test_skill_manifest.py`

- [ ] **Step 1: Write failing API/static/runtime tests.**

  Assert `GET /api/skills` returns enabled public projections only, never handler/Agent/service internals; the CC allow-list equals catalog `agent_skill` values and rejects unknown names; browser source has no complete static business menu and keeps manual input when API loading fails.

- [ ] **Step 2: Run tests to capture the current hard-coded behavior.**

  Run: `.venv/bin/pytest -q tests/test_api.py tests/test_subpath_frontend.py tests/test_skill_manifest.py -k 'skill or slash or menu or runtime'`

  Expected: failures showing no `/api/skills`, hard-coded `assistantSlashCommands`, or independent `ALLOWED_PROJECT_SKILLS`.

- [ ] **Step 3: Implement API projection, catalog-derived runtime validation, and dynamic menu loading.**

  Load public enabled items into the existing menu structure. On request failure hide the menu and leave typed commands, existing card actions, and server parsing available; do not add a second full or minimal static catalog. Unknown `output_kind` must route to `default_markdown`.

- [ ] **Step 4: Run focused API/frontend/runtime tests.**

  Run: `.venv/bin/pytest -q tests/test_api.py tests/test_subpath_frontend.py tests/test_skill_manifest.py -k 'skill or slash or menu or runtime'`

  Expected: API exposure, no-static-catalog, CC allow-list, and Markdown fallback tests pass.

## Stage 3: Router, Schedule Confirmation, Timezone, and Factcheck

### Task 5: Implement deterministic/model SkillRouter and shared execution path

**Files:**
- Create: `personal_news_agent/services/skill_router.py`
- Modify: `personal_news_agent/services/chat.py`
- Modify: `personal_news_agent/services/factory.py`
- Modify: `personal_news_agent/api/schemas.py`
- Test: `tests/test_skill_router.py`, `tests/test_services.py`, `tests/test_api.py`

- [ ] **Step 1: Write failing route and execution-path tests.**

  Use a fake `LLMClient.structured` and assert:

  ```python
  await router.route(message, context=ctx, model_key="custom", use_llm=False)
  ```

  never invokes the fake model; explicit slash and confidence `>=0.90` rules still route; `use_llm=True` passes the exact `model_key`; model confidence `<0.82`, invalid JSON, timeout, and non-news comparisons return `skill_id=None, source="fallback"`. Assert `SkillRoute.arguments` uses `Field(default_factory=dict)` and two instances do not share a dictionary. Assert sync `/api/chat` and SSE `/api/chat/stream` invoke the same `_route_and_execute_skill` path.

- [ ] **Step 2: Run router tests and observe failure.**

  Run: `.venv/bin/pytest -q tests/test_skill_router.py tests/test_services.py -k 'route or chat_events or model_key or use_llm'`

  Expected: missing Router, missing arguments contract, and divergent chat branches.

- [ ] **Step 3: Implement route ordering and adapters.**

  Use exact slash lookup first, high-precision Chinese rule combinations second, model classification only when `use_llm=True`, and the existing non-model path when disabled/uncertain. Define `ChangedArguments`, `CompareArguments`, and `ScheduleArguments(raw_request)` with `extra="forbid"`; fill topic from context only after route parsing. Add `_route_and_execute_skill()` and make both chat entry points call it. Schedule natural language returns a blocked confirmation preview; explicit slash uses `execute_text` compatibility behavior.

- [ ] **Step 4: Run focused router/chat/API tests.**

  Run: `.venv/bin/pytest -q tests/test_skill_router.py tests/test_services.py tests/test_api.py -k 'route or chat or stream or model_key or use_llm'`

  Expected: route confidence, model gate, argument factory, and sync/SSE parity tests pass.

### Task 6: Add application timezone and refactor schedule parsing into preview/create steps

**Files:**
- Create: `personal_news_agent/services/time_context.py`
- Modify: `personal_news_agent/config.py`
- Modify: `.env.example`
- Modify: `personal_news_agent/services/tasks.py`
- Modify: `personal_news_agent/services/factory.py`
- Test: `tests/test_timezone_policy.py`, existing schedule tests

- [ ] **Step 1: Write failing non-Beijing timezone and preview tests.**

  Run the service under `TZ=UTC` with a fixed clock and assert the default application timezone remains `Asia/Shanghai`; cron `09:00`, relative dates, `next_run_at`, due execution, changed baseline helpers, and preview display all use the same zone. Assert a task-level `timezone` field is rejected and invalid `PNA_APP_TIMEZONE` fails startup. Assert `prepare_schedule_preview` does not call `store.create_task`.

- [ ] **Step 2: Run the focused timezone/schedule tests and observe current `LOCAL_TZ` failures.**

  Run: `TZ=UTC .venv/bin/pytest -q tests/test_timezone_policy.py tests/test_services.py -k 'schedule or task or timezone'`

  Expected: failure while `tasks.py` uses server-local `LOCAL_TZ` and lacks a preview API.

- [ ] **Step 3: Implement one application timezone and split schedule operations.**

  Add `Settings.app_timezone` from `PNA_APP_TIMEZONE` with default `Asia/Shanghai` and `ZoneInfo` validation. Inject one `ZoneInfo` into schedule/router/change/API display services. Replace all `LOCAL_TZ` reads, keep UTC comparison for due tasks, and calculate cron in application time. Add `prepare_schedule_preview(...)` and `create_from_schedule_preview(...)`; preserve `/api/tasks/schedule` as immediate compatibility entry.

- [ ] **Step 4: Run timezone and existing schedule tests.**

  Run: `TZ=UTC .venv/bin/pytest -q tests/test_timezone_policy.py tests/test_services.py -k 'schedule or task or timezone'`

  Expected: fixed-clock tests pass under UTC and existing task creation/execution tests remain green.

### Task 7: Implement process-local schedule confirmation API and cards

**Files:**
- Create: `personal_news_agent/services/schedule_confirmation.py`
- Modify: `personal_news_agent/api/schemas.py`
- Modify: `personal_news_agent/api/routes.py`
- Modify: `personal_news_agent/services/chat.py`
- Modify: `personal_news_agent/static/shared.js`
- Modify: `personal_news_agent/static/web.js`
- Modify: `personal_news_agent/static/mobile.js`
- Test: `tests/test_schedule_confirmation.py`, `tests/test_api.py`, `tests/test_subpath_frontend.py`

- [ ] **Step 1: Write failing confirmation state-machine and API/UI tests.**

  Cover `POST /api/tasks/schedule/confirm` and `/cancel`; preview fields; `secrets.token_urlsafe(32)` generation; token SHA-256 storage; 15-minute absolute/monotonic expiry; user/conversation/confirmation binding; preview digest; `pending -> consuming -> confirmed/cancelled`; replay, concurrent click, expiry, cancellation, and restart invalidation; no task count change before confirm; only one task after confirm. Assert request models contain `user_id`, `conversation_id`, `confirmation_id`, and `confirmation_token`; UI disables both buttons and does not restore tokens from history.

- [ ] **Step 2: Run the confirmation tests and observe missing endpoints/service.**

  Run: `.venv/bin/pytest -q tests/test_schedule_confirmation.py tests/test_api.py tests/test_subpath_frontend.py -k 'schedule or confirmation'`

  Expected: collection failures or 404s because action schemas, routes, token registry, and renderer do not exist.

- [ ] **Step 3: Implement the token registry and action routes.**

  Use an `asyncio.Lock`-protected bounded TTL registry storing only token digest plus bound preview/digests and replay result. Confirm atomically marks `pending` as `consuming`, creates from the frozen preview without re-running parsing, stores the result, and returns the same task on replay. Return 403/404/409/410 for binding, unknown, concurrent/invalid state, and expired tokens. Persist only sanitized preview/status in the blocked turn; never persist or log the bearer token.

- [ ] **Step 4: Implement Web/Mobile confirmation renderer and run focused tests.**

  Add `output_kind=schedule_confirmation` rendering of preview, timezone, next run, scope, report sections, and confirm/cancel buttons. Disable both buttons on first click, map 403/404/409/410 to clear text, and make history cards non-actionable. Run the focused command from Step 2; expected all state/API/static assertions pass.

### Task 8: Enforce factcheck offline policy

**Files:**
- Modify: `personal_news_agent/skills/factcheck.py`
- Modify: `personal_news_agent/services/factcheck.py`
- Modify: `personal_news_agent/services/cc_runtime.py` only if tool-event accounting needs the existing gate exposed
- Modify: result models for top-level envelope fields
- Test: focused factcheck tests in `tests/test_services.py`, `tests/test_api.py`

- [ ] **Step 1: Replace the stale forced-remote assertion with two failing offline cases.**

  With `allow_web_search=False`, assert external provider, CC application web tool, and builtin WebSearch call counts are all zero. Assert sufficient local evidence yields `status=success` and `research_mode=local_only`; insufficient evidence yields `status=degraded`, `fallback_reason=web_search_disabled`, and `research_mode=local_only`.

- [ ] **Step 2: Run the factcheck tests and observe the forced `include_remote=True` failure.**

  Run: `.venv/bin/pytest -q tests/test_services.py tests/test_api.py -k 'factcheck'`

  Expected: current `FactCheckSkill.run()` passes `include_remote=True`, causing the offline regression test to fail.

- [ ] **Step 3: Pass `SkillContext.allow_web_search` through all factcheck branches.**

  Guard CC runtime, external provider fallback, and builtin WebSearch with the same boolean. Build the finalized top-level status from evidence sufficiency, not from the fact that the user chose offline mode.

- [ ] **Step 4: Run focused factcheck and no-web regression tests.**

  Run: `.venv/bin/pytest -q tests/test_services.py tests/test_api.py -k 'factcheck'`

  Expected: no external call occurs offline, and success/degraded status branches match the approved contract.

## Stage 4: `/changed`

### Task 9: Implement ChangeDetectionService, handler, Agent Skill, and renderer

**Files:**
- Create: `personal_news_agent/services/change_detection.py`
- Create: `personal_news_agent/skills/changed.py`
- Create: `personal_news_agent/skills/result_models.py` additions if not already present
- Create: `.claude/skills/news-change-digest/SKILL.md`
- Create: `.claude/skills/news-change-digest/agents/openai.yaml`
- Modify: `personal_news_agent/services/factory.py`, `services/chat.py`, `skills/catalog.py`, `static/shared.js`, `static/web.js`, `static/mobile.js`
- Test: `tests/test_change_detection.py`, `tests/test_skill_router.py`, `tests/test_subpath_frontend.py`

- [ ] **Step 1: Write failing change-digest tests.**

  Cover current-turn baseline priority; explicit `昨天`/`最近一周` override; absent baseline defaults to past 24 hours with explicit label; title-only/new-URL/identical-content changes become `repeated_reports`; material facts/status/numbers/corrections are separated; no material change returns `change_status=no_material_change`; Agent failure returns safe new-report-only data with top-level `status=degraded`; all conclusion evidence indices belong to the current ledger.

- [ ] **Step 2: Run the focused change tests and observe missing imports.**

  Run: `.venv/bin/pytest -q tests/test_change_detection.py tests/test_skill_router.py -k 'changed or baseline'`

  Expected: failures because the Service, handler, result model, and Agent directory do not exist.

- [ ] **Step 3: Implement deterministic baseline/diff and bounded Agent enrichment.**

  Query current conversation evidence first, apply explicit time range second, otherwise use `now - 24h` in the application timezone. Group same content/hash/near-duplicate reports before classifying material change. Validate Agent JSON against `ChangeDigestData`, drop unknown evidence references, and use safe reporting fallback when Agent unavailable.

- [ ] **Step 4: Wire route, result envelope, and `change_digest` renderer.**

  Register `changed` as read-only public with `arguments_model=ChangedArguments`; route through `execute_structured`; render structured data and fall back to Markdown for unknown/card errors.

- [ ] **Step 5: Run Stage 4 focused tests.**

  Run: `.venv/bin/pytest -q tests/test_change_detection.py tests/test_skill_router.py tests/test_subpath_frontend.py -k 'changed or baseline or renderer'`

  Expected: all changed baseline, materiality, fallback, evidence, routing, and static renderer tests pass.

## Stage 5: `/compare`

### Task 10: Implement CoverageComparisonService, handler, Agent Skill, and renderer

**Files:**
- Create: `personal_news_agent/services/coverage_comparison.py`
- Create: `personal_news_agent/skills/compare.py`
- Create: `.claude/skills/news-coverage-compare/SKILL.md`
- Create: `.claude/skills/news-coverage-compare/agents/openai.yaml`
- Modify: `personal_news_agent/services/factory.py`, `services/chat.py`, `skills/catalog.py`, `static/shared.js`, `static/web.js`, `static/mobile.js`
- Test: `tests/test_coverage_comparison.py`, `tests/test_skill_router.py`, `tests/test_subpath_frontend.py`

- [ ] **Step 1: Write failing comparison tests.**

  Cover exact duplicate, syndicated same稿, and high-similarity grouping; independent reporting remains separate; shared atomic facts go to `common_facts`; source-only claims go to `unique_claims`; actual incompatible claims go to `conflicts`; wording, headline, tone, and stance differences go to `framing_differences` or no difference; fewer than two independent sources returns `comparison_status=insufficient`; Agent failure preserves source groups and basic differences with top-level `status=degraded`; every conclusion points to current evidence.

- [ ] **Step 2: Run focused comparison tests and observe missing implementation.**

  Run: `.venv/bin/pytest -q tests/test_coverage_comparison.py tests/test_skill_router.py -k 'compare or source_group'`

  Expected: import/route failures because the Service, handler, model, and Agent Skill are absent.

- [ ] **Step 3: Implement conservative source grouping and claim classification.**

  Normalize URL/content/title, merge exact and near-duplicate distribution with an explanatory group reason, and require independent source identity before counting corroboration. Separate atomic fact identity from framing text before deciding conflict. Validate Agent output against `CoverageCompareData` and reject unledgered evidence.

- [ ] **Step 4: Wire route, envelope, and `coverage_compare` renderer.**

  Register `compare` as read-only public with `CompareArguments`; execute via structured Registry path and return `comparison_status`, never a second `status` field in business data.

- [ ] **Step 5: Run Stage 5 focused tests.**

  Run: `.venv/bin/pytest -q tests/test_coverage_comparison.py tests/test_skill_router.py tests/test_subpath_frontend.py -k 'compare or source_group or renderer'`

  Expected: source grouping, conflict/framing boundaries, insufficiency/degraded fallback, evidence, routing, and renderer tests pass.

## Stage 6: Exposure Cleanup, Compatibility, and Regression

### Task 11: Finish public/internal/admin/context-action projections and documentation

**Files:**
- Modify: `personal_news_agent/skills/catalog.py`, `skills/README.md`, `README.md`
- Modify: `personal_news_agent/static/shared.js`, `static/web.js`, `static/mobile.js`
- Modify: `personal_news_agent/api/routes.py` only for final projection/query behavior
- Test: `tests/test_api.py`, `tests/test_subpath_frontend.py`, `tests/test_registry.py`

- [ ] **Step 1: Write failing exposure/compatibility assertions.**

  Assert public API/menu contains brief, factcheck, map, report, changed, compare, and advanced schedule; related is callable but context-only; sources is admin-only; internal research Agent is not a public candidate; old `/related`, `/sources`, seven canonical commands, aliases, history Markdown, and existing card actions remain usable.

- [ ] **Step 2: Run focused compatibility tests and inspect any stale hard-coded list.**

  Run: `.venv/bin/pytest -q tests/test_api.py tests/test_subpath_frontend.py tests/test_registry.py -k 'skill or slash or compatibility or exposure'`

  Expected: only stale projection/list assertions fail.

- [ ] **Step 3: Implement the smallest projection/menu changes.**

  Keep context-action behavior in the relevant page code, remove only duplicate full command metadata, and retain manual command input on `/api/skills` failure. Do not migrate or delete old routes.

- [ ] **Step 4: Run focused compatibility tests.**

  Run the command from Step 2; expected all exposure and old-command assertions pass.

### Task 12: Run full regression and update the development document

**Files:**
- Modify: `docs/development/skill-system-upgrade.md`
- Test: all existing `tests/` plus every new focused test

- [ ] **Step 1: Run the complete focused suite.**

  Run:

  ```bash
  .venv/bin/pytest -q \
    tests/test_skill_manifest.py tests/test_registry.py tests/test_result_contracts.py \
    tests/test_skill_router.py tests/test_schedule_confirmation.py tests/test_timezone_policy.py \
    tests/test_change_detection.py tests/test_coverage_comparison.py \
    tests/test_services.py tests/test_api.py tests/test_subpath_frontend.py
  ```

  Expected: zero failures; report exact pass/deselected counts.

- [ ] **Step 2: Run the full repository regression.**

  Run: `.venv/bin/pytest -q`

  Expected: all existing and new tests pass. If an environment/provider/browser limitation prevents a test, record the exact command, error, and unverified behavior rather than weakening the test.

- [ ] **Step 3: Run static/document checks.**

  Run:

  ```bash
  rg -n 'TBD|TODO|FIXME|data\.status|联网关闭一律|完整静态.*fallback|最小.*fallback' docs/development/skill-system-upgrade.md
  git diff --check
  git status --short --branch
  ```

  Expected: no stale design terms; only intentional user/worktree changes plus this document and implementation files are listed; no whitespace errors.

- [ ] **Step 4: Update implementation status in the development document.**

  Add only verified implementation state: changed files, exact test commands/results, compatibility behavior, offline/timezone/schedule degradation boundaries, and any genuinely incomplete items. Do not mark a stage complete based on planned tests that were not run.

- [ ] **Step 5: Stop for user acceptance.**

  Do not commit, push, create a PR, or start additional refactoring. Report implementation files, tests, actual results, compatibility/degradation behavior, and remaining gaps, then wait for acceptance.

## Plan Self-Review Against Approved Document

- Manifest fields, exposure roles, Registry mapping, CC allow-list derivation, public API projection, frontend loading, and consistency validation are covered by Tasks 1–4 and 11.
- Router ordering, `model_key`, `use_llm`, confidence fallback, one-Skill V1 behavior, parameter models/adapters, and sync/SSE convergence are covered by Task 5.
- Natural-language schedule preview, confirmation/cancellation endpoints, token TTL/binding/digests, replay/concurrency/restart behavior, no-migration storage, timezone policy, frontend cards, and task-count assertions are covered by Tasks 6–7.
- Factcheck `allow_web_search` enforcement and the finalized success/degraded offline states are covered by Task 8.
- Changed baseline/materiality/repeated-report rules, `change_status`, safe Agent degradation, evidence ledger, Service/Skill/card files are covered by Task 9.
- Compare repost grouping, common/unique/conflict/framing distinctions, `comparison_status`, insufficient/degraded output, evidence binding, Service/Skill/card files are covered by Task 10.
- Compatibility, rollback-safe exposure changes, full regression, document implementation status, and the no-VCS gate are covered by Tasks 11–12.
- No plan step adds a database migration, provider, multi-Skill orchestration, management backend, unrelated refactor, commit, push, or PR.
