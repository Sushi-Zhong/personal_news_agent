# Skill State And Analysis Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear all topic presentation state for new conversations and make compare/changed use valid temporal baselines and robust structured output parsing.

**Architecture:** Keep the correction scoped to the two new analysis services and existing frontend reset paths. Share small deterministic helpers for evidence time filtering and JSON extraction without changing API schemas or unrelated chat behavior.

**Tech Stack:** Vanilla JavaScript, Python 3.12, Pydantic, Pytest.

---

### Task 1: New conversation presentation reset

**Files:**
- Modify: `tests/test_subpath_frontend.py`
- Modify: `personal_news_agent/static/web.js`
- Modify: `personal_news_agent/static/mobile.js`

- [x] Add a failing source-contract test requiring desktop `startNewTopicConversation()` to call the empty-topic renderer and mobile reset to clear its response-scoped feed and counts.
- [x] Run the focused test and confirm it fails for the missing reset calls.
- [x] Add the minimal reset calls using the existing render paths.
- [x] Run the focused test and confirm it passes.

### Task 2: Analysis evidence time window and changed baseline

**Files:**
- Modify: `tests/test_coverage_comparison.py`
- Modify: `tests/test_change_detection.py`
- Modify: `personal_news_agent/services/coverage_comparison.py`
- Modify: `personal_news_agent/services/change_detection.py`

- [x] Add failing tests proving compare excludes stale and undated local evidence.
- [x] Add a failing test proving changed skips coverage/change result turns when selecting a baseline.
- [x] Run the focused tests and confirm both fail for the expected reasons.
- [x] Implement scoped evidence-window filtering and derived-result exclusion.
- [x] Run the focused tests and confirm they pass.

### Task 3: Robust Agent JSON and restrained status copy

**Files:**
- Modify: `tests/test_coverage_comparison.py`
- Modify: `tests/test_change_detection.py`
- Modify: `tests/test_skill_frontend_contract.py`
- Modify: `personal_news_agent/services/coverage_comparison.py`
- Modify: `personal_news_agent/services/change_detection.py`
- Modify: `personal_news_agent/static/shared.js`

- [x] Add failing tests for explanatory prose plus one fenced JSON object.
- [x] Add a failing frontend contract test rejecting raw fallback and source-group codes.
- [x] Run the focused tests and confirm they fail.
- [x] Implement deterministic fenced-object extraction, correct failure classification, and Chinese UI mappings.
- [x] Run all focused tests and confirm they pass.

### Task 4: Cache and complete verification

**Files:**
- Modify: `personal_news_agent/static/home.html`
- Modify: `personal_news_agent/static/home.js`
- Modify: exact cache assertions in frontend tests.

- [x] Bump the asset version once after frontend changes.
- [x] Run Skill, frontend, API, registry, and search-related tests.
- [x] Run the complete Pytest suite, JavaScript syntax checks, and `git diff --check`.
- [x] Review the final diff without staging, committing, pushing, merging, or cleaning unrelated files.
