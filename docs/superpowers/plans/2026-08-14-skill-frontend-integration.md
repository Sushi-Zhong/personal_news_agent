# Skill Frontend Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the Manifest-driven Skill flow across menu discovery, desktop/mobile submission, structured rendering styles, and regression coverage.

**Architecture:** `shared.js` owns the asynchronously loaded public Skill catalog and exposes one async membership helper. Existing page-specific commands keep their compatibility branches; unmatched public Skill commands pass through unchanged to the chat backend. Structured result cards share restrained editorial CSS tokens across light, dark, and mobile layouts.

**Tech Stack:** Vanilla JavaScript, CSS, FastAPI public Skill catalog, Pytest source-contract and service tests.

---

### Task 1: Lock the dynamic Skill submission contract

**Files:**
- Modify: `tests/test_skill_frontend_contract.py`

- [ ] **Step 1: Write failing tests for catalog readiness, alias search, and page passthrough**

```python
WEB = ROOT / "personal_news_agent" / "static" / "web.js"
MOBILE = ROOT / "personal_news_agent" / "static" / "mobile.js"

def test_public_skill_catalog_drives_desktop_and_mobile_submission():
    shared = SHARED.read_text(encoding="utf-8")
    web = WEB.read_text(encoding="utf-8")
    mobile = MOBILE.read_text(encoding="utf-8")
    assert "let assistantSkillsReady" in shared
    assert "async function isPublicAssistantSkillCommand" in shared
    assert "await assistantSkillsReady" in shared
    assert 'await isPublicAssistantSkillCommand(command.name)' in web
    assert 'return sendChatIntoTurn(message, assistantNode);' in web
    assert 'await isPublicAssistantSkillCommand(command.name)' in mobile

def test_slash_menu_searches_aliases_and_descriptions():
    shared = SHARED.read_text(encoding="utf-8")
    assert 'command.aliases.join(" ")' in shared
    assert "command.description" in shared
```

- [ ] **Step 2: Run the tests and confirm they fail because the helper and passthrough do not exist**

Run: `.venv/bin/pytest -q tests/test_skill_frontend_contract.py -x`

Expected: FAIL in the new catalog/submission contract tests.

### Task 2: Implement generic public Skill passthrough

**Files:**
- Modify: `personal_news_agent/static/shared.js:2243-2305`
- Modify: `personal_news_agent/static/web.js:230-329`
- Modify: `personal_news_agent/static/mobile.js:328-422`

- [ ] **Step 1: Make catalog loading awaitable and expose canonical/alias membership**

```javascript
let assistantSkillsReady = null;

async function isPublicAssistantSkillCommand(name) {
  await assistantSkillsReady;
  const token = `/${String(name || "").replace(/^\//, "").toLowerCase()}`;
  return assistantSlashCommands.some((command) =>
    `/${command.name}` === token || command.aliases.some((alias) => alias.toLowerCase() === token),
  );
}

assistantSkillsReady = loadAssistantSkills();
```

- [ ] **Step 2: Include aliases and descriptions in menu filtering**

```javascript
visibleCommands = assistantSlashCommands.filter((command) =>
  `${command.name} ${command.label} ${command.description} ${command.aliases.join(" ")}`
    .toLowerCase()
    .includes(query),
);
```

- [ ] **Step 3: Add the generic fallback before the unknown-command message on both pages**

```javascript
if (await isPublicAssistantSkillCommand(command.name)) {
  return sendChatIntoTurn(message, assistantNode);
}
```

- [ ] **Step 4: Run the contract tests and confirm they pass**

Run: `.venv/bin/pytest -q tests/test_skill_frontend_contract.py -x`

Expected: all tests pass.

### Task 3: Add restrained structured-result styling

**Files:**
- Modify: `tests/test_subpath_frontend.py`
- Modify: `personal_news_agent/static/newsroom.css`

- [ ] **Step 1: Write a failing style contract test**

```python
def test_newsroom_structured_skill_results_share_editorial_theme_styles():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    for selector in (
        ".change-digest-card",
        ".coverage-compare-card",
        ".schedule-confirmation-card",
        ".skill-evidence-list",
    ):
        assert selector in styles
    assert ".newsroom-shell.console-dark .change-digest-card" in styles
    assert ".newsroom-mobile .change-digest-card" in styles
```

- [ ] **Step 2: Run the style contract and confirm it fails for missing selectors**

Run: `.venv/bin/pytest -q tests/test_subpath_frontend.py::test_newsroom_structured_skill_results_share_editorial_theme_styles`

Expected: FAIL because structured card selectors are absent.

- [ ] **Step 3: Add shared editorial card, section, status, evidence, action, dark, and mobile rules**

Use existing tokens (`--paper`, `--ink`, `--ink-soft`, `--line`, `--accent`, `--accent-soft`) with flat backgrounds, 14-16px radii, body/control typography variables, and no gradients.

- [ ] **Step 4: Run the style contract and frontend suite**

Run: `.venv/bin/pytest -q tests/test_subpath_frontend.py -x`

Expected: all tests pass.

### Task 4: Bust caches and verify the full flow

**Files:**
- Modify: `personal_news_agent/static/home.html`
- Modify: `personal_news_agent/static/home.js`
- Modify: `tests/test_subpath_frontend.py`
- Modify: `tests/test_services.py`

- [ ] **Step 1: Update the shared static asset version to `20260814-skill-flow-1`**

Update `newsroom.css`, `home.js`, and the `home.js` dynamic asset default together; update exact-version assertions.

- [ ] **Step 2: Run focused frontend and Skill tests**

Run: `.venv/bin/pytest -q tests/test_skill_frontend_contract.py tests/test_subpath_frontend.py tests/test_api.py tests/test_registry.py tests/test_skill_manifest.py tests/test_skill_router.py tests/test_change_detection.py tests/test_coverage_comparison.py -x`

Expected: all tests pass.

- [ ] **Step 3: Run JavaScript and diff validation**

Run: `node --check personal_news_agent/static/shared.js && node --check personal_news_agent/static/web.js && node --check personal_news_agent/static/mobile.js && node --check personal_news_agent/static/home.js && git diff --check`

Expected: exit code 0 with no syntax or whitespace errors.

- [ ] **Step 4: Verify the running service serves the new catalog and asset version**

Check `/api/skills`, `/web`, and the versioned CSS/JS responses. Confirm `changed` and `compare` are public, and that the served scripts contain the generic public-Skill passthrough.

- [ ] **Step 5: Inspect desktop light/dark and mobile layouts in the browser**

Open the existing local app session, trigger the `/` menu, and inspect structured-result fixtures or real Skill responses for overflow, typography, evidence links, and theme contrast.

No commit is included in this plan because the working tree contains a larger user-owned Skill refactor and the user did not request a Git operation.
