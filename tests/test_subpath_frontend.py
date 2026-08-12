from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "personal_news_agent" / "static"


@pytest.mark.parametrize("name", ["landing.html", "auth.html", "index.html", "home.html", "mobile.html"])
def test_frontend_assets_and_navigation_do_not_escape_reverse_proxy_prefix(name: str):
    source = (STATIC_DIR / name).read_text(encoding="utf-8")
    assert 'href="/' not in source
    assert 'src="/' not in source


def test_shared_client_preserves_pna_prefix_for_api_requests():
    source = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    assert 'const prefix = "/pna"' in source
    assert "fetch(appUrl(path)" in source
    assert "timeoutMs: 15000" in source
    assert "controller.abort()" in source
    assert "短信服务响应超时" in source
    assert "button.disabled = true;\n      if (submit) submit.disabled = true;" not in source
    assert "你仍可点击发送重试" in source
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    auth = (STATIC_DIR / "auth.html").read_text(encoding="utf-8")
    auth_script = (STATIC_DIR / "auth.js").read_text(encoding="utf-8")
    mobile_script = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")
    assert "home.js?v=20260812-newsroom-8" in home
    assert "shared.js?v=20260810-phone-controls-2" in auth
    assert "ensureRegistrationChallenge(form, status)" in auth_script
    assert "ensureRegistrationChallenge(form, status)" in mobile_script
    assert "function ensureRegistrationChallenge" in source


def test_server_templates_keep_nginx_and_web_port_aligned():
    runtime_env = (ROOT / "deploy" / "env.ext.server.example").read_text(encoding="utf-8")
    nginx = (ROOT / "deploy" / "nginx" / "personal-news-location.conf.example").read_text(encoding="utf-8")
    assert 'PNA_WEB_PORT="22053"' in runtime_env
    assert "location /pna/" in nginx
    assert "proxy_pass http://127.0.0.1:22053/;" in nginx
    assert "X-Forwarded-Prefix /pna" in nginx


def test_html_routes_disable_cache_and_expose_frontend_revision():
    routes = (ROOT / "personal_news_agent" / "api" / "routes.py").read_text(encoding="utf-8")
    assert 'FRONTEND_REVISION = "20260811-topic-pulse-6"' in routes
    assert '"Cache-Control": "no-store, max-age=0"' in routes
    assert '"frontend_revision": FRONTEND_REVISION' in routes


def test_console_theme_has_dark_drawers_readable_content_and_responsive_rails():
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    assert "styles.css?v=20260811-topic-pulse-6" in home
    assert ".console-shell .agent-drawer" in styles
    assert "background: rgba(10, 28, 45, 0.96)" in styles
    assert ".console-shell .assistant-markdown h3" in styles
    assert ".console-shell .chat-event strong" in styles
    assert "@media (max-width: 1380px)" in styles
    assert "position: sticky" in styles


def test_console_can_switch_theme_without_breaking_fixed_editorial_columns():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    app = (STATIC_DIR / "home.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'class="newsroom-grid news-console"' in home
    assert 'class="newsroom-left"' in home
    assert 'class="newsroom-center"' in home
    assert 'class="newsroom-right"' in home
    assert 'id="themeToggle"' in home
    assert 'aria-label="切换浅色背景"' in home
    assert "themePreferenceKey" in app
    assert 'document.body.classList.toggle("console-light"' in app
    assert ".newsroom-shell.console-dark" in styles
    grid_rules = styles.split(".newsroom-grid", 1)[1].split("}", 1)[0]
    assert "display: grid" in grid_rules
    assert "height: calc(100dvh - var(--newsroom-header))" in grid_rules
    assert "overflow: hidden" in grid_rules
    rail_rules = styles.split(".newsroom-left,", 1)[1].split("}", 1)[0]
    assert "height: 100%" in rail_rules
    assert "overflow-y: auto" in rail_rules
    assert "min-width: 0" in rail_rules
    assert "overscroll-behavior: contain" in rail_rules
    assert ".main-dialog" in styles
    assert "border-radius: var(--radius-lg)" in styles


def test_newsroom_redesign_has_stable_editorial_columns_and_non_disruptive_tools():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    app = (STATIC_DIR / "home.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'static/newsroom.css?v=20260812-newsroom-8' in home
    assert '20260812-newsroom-8' in app
    assert 'data-body-class="web-shell console-shell newsroom-shell"' in home
    assert 'data-body-class="mobile-shell newsroom-mobile"' in home
    assert 'class="newsroom-left"' in home
    assert 'class="newsroom-center"' in home
    assert 'class="newsroom-right"' in home
    assert 'class="analysis-board"' in home
    assert 'class="workspace-tools-popover"' not in home
    assert "--newsroom-left: 256px" in styles
    assert "--newsroom-right: 320px" in styles
    assert "grid-template-columns: var(--newsroom-left) minmax(0, 1fr) var(--newsroom-right)" in styles
    assert ".newsroom-left," in styles
    assert "overflow-y: auto" in styles
    assert ".workspace-tools-popover" not in styles
    assert "@media (prefers-reduced-motion: reduce)" in styles
    assert "@media (prefers-reduced-transparency: reduce)" in styles
    assert "@media (prefers-contrast: more)" in styles
    assert 'window.matchMedia("(max-width: 1024px)")' in app


def test_newsroom_surface_uses_concise_blue_only_copy_and_controls():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    for label in ("LIBRARY", "NOW", "CONTEXT", "INTELLIGENCE", "ANALYSIS", "AI NEWSROOM"):
        assert label not in home
    for symbol in ("↻", "◇"):
        assert symbol not in home
    assert "News Agent" not in shared
    assert "--accent: #3d73d6" in styles
    assert "--accent-hover: #3568c4" in styles
    assert "#146b4f" not in styles
    assert "#5fc69a" not in styles


def test_newsroom_chat_uses_outer_scrolling_and_compact_composer():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-center" in styles
    assert ".newsroom-shell .messages.hero-messages.chat-stream" in styles
    assert "max-height: none" in styles
    assert "overflow-y: visible" in styles
    assert "min-height: 0" in styles
    assert ".composer-dock .dialog-command" in styles
    assert "min-height: 34px" in styles
    assert "padding: 4px" in styles


def test_newsroom_dark_theme_covers_all_dynamic_surfaces_with_blue_tokens():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-shell.console-dark .main-dialog" in styles
    assert ".newsroom-shell.console-dark .chat-assistant" in styles
    assert ".newsroom-shell.console-dark .assistant-markdown" in styles
    assert ".newsroom-shell.console-dark input" in styles
    assert ".newsroom-shell.console-dark .dialog-command" in styles
    assert ".newsroom-shell.console-dark .chat-assistant" in styles
    assert "#0b1220" in styles
    assert "rgba(61, 115, 214, 0.18)" in styles


def test_newsroom_keeps_one_vertical_scroll_owner_and_hides_empty_evidence():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'class="system-strip"' not in home
    assert 'data-es-status' not in home
    assert 'data-mysql-status' not in home
    assert 'data-source-count' not in home
    assert 'document.querySelector("[data-es-status]")' not in web
    assert 'document.querySelector("[data-mysql-status]")' not in web
    assert 'document.querySelector("[data-source-count]")' not in web
    assert 'target.hidden = true' in web
    assert 'target.hidden = false' in web
    assert ".newsroom-shell .messages.hero-messages.chat-stream" in styles
    assert "overflow-y: visible" in styles
    assert ".newsroom-center" in styles
    assert "--font-newsroom: -apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Helvetica Neue\", sans-serif" in styles


def test_newsroom_typography_scale_and_floating_composer_are_consistent():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    for token in ("--type-caption", "--type-meta", "--type-control", "--type-body", "--type-section", "--type-title"):
        assert token in styles
    assert "--font-newsroom: -apple-system, BlinkMacSystemFont, \"PingFang SC\", \"Helvetica Neue\", sans-serif" in styles
    composer = styles.split(".composer-dock {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in composer
    assert "margin: 0" in composer
    assert "border-radius: 16px" in composer
    assert "backdrop-filter: blur(22px) saturate(150%)" in composer
    assert "calc(var(--newsroom-reading-gutter) * -1)" not in composer
    assert ".newsroom-shell .assistant-markdown" in styles
    assert "font-size: var(--type-body)" in styles


def test_newsroom_reading_insets_and_theme_specific_controls_remain_legible():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-shell .round-add" in styles
    assert "font-size: var(--type-control)" in styles
    assert ".newsroom-shell .assistant-markdown > .answer-section" in styles
    assert "padding: 20px 18px" in styles
    assert ".newsroom-shell .assistant-markdown .markdown-table-wrap" in styles
    assert "padding: 0 2px" in styles
    assert ".newsroom-shell.console-light .trace-step strong" in styles
    assert "color: #172033" in styles
    assert ".newsroom-shell.console-dark .workspace-tools > summary" not in styles
    assert ".newsroom-shell.console-dark .config-dialog" in styles


def test_newsroom_content_blocks_do_not_reserve_empty_vertical_space():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    assert 'target.dataset.view = viewType' in web
    assert '.analysis-board .topic-visual[data-view="event-line"]' in styles
    workbench = styles.split(".newsroom-shell .analysis-board .topic-workbench", 1)[1].split("}", 1)[0]
    assert "display: flex" in workbench
    assert "flex-direction: column" in workbench
    assert "grid-template-rows" not in workbench
    event_view = styles.split('.analysis-board .topic-visual[data-view="event-line"]', 1)[1].split("}", 1)[0]
    assert "height: auto" in event_view
    assert "min-height: 0" in event_view
    assert "overflow: visible" in event_view
    event_line = styles.split(".analysis-board .console-event-line", 1)[1].split("}", 1)[0]
    assert "max-height: none" in event_line
    assert "overflow: visible" in event_line
    assert ".newsroom-shell .chat-user" in styles
    user_turn = styles.rsplit(".newsroom-shell .chat-user {", 1)[1].split("}", 1)[0]
    assert "width: auto" in user_turn
    assert "background: transparent" in user_turn
    assert ".newsroom-shell .chat-user .chat-bubble" in styles


def test_newsroom_relation_graph_fits_without_inner_scroll():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    relation_view = styles.split('.analysis-board .topic-visual[data-view="relation-graph"]', 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in relation_view
    assert "min-height: 360px" in relation_view
    assert "height: clamp(360px, 44vh, 520px)" in relation_view
    assert "min-height: 360px" in relation_view
    graph = styles.split(".analysis-board .topic-relation-graph", 1)[1].split("}", 1)[0]
    assert "height: 100%" in graph
    assert "min-height: 0" in graph
    assert "overflow: visible" in graph
    assert 'preserveAspectRatio="xMidYMid meet"' in web
    assert "const graphBox = { width: 900, height: 540 };" in web
    assert "graphPositions(nodes, graphBox.width, graphBox.height)" in web
    assert "const safeRadius = Math.max(52, Math.min(width, height) * 0.11);" in web


def test_newsroom_chat_scrolls_to_bottom_after_new_message():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")

    scroll_helper = shared.split("function scrollChatToBottom", 1)[1].split("function upsertResearchTrace", 1)[0]
    assert "closestScrollContainer" in scroll_helper
    assert ".newsroom-center" in scroll_helper
    assert "scrollNode.scrollHeight - scrollNode.clientHeight" in scroll_helper
    assert "scrollNode.scrollHeight" in scroll_helper
    assert "scrollNode.scrollTo" in scroll_helper
    assert "Math.max" in scroll_helper


def test_newsroom_chat_auto_scroll_sticks_during_streaming_updates():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")

    scroll_helper = shared.split("function scrollChatToBottom", 1)[1].split("function upsertResearchTrace", 1)[0]
    assert "const chatAutoScrollState = new WeakMap();" in shared
    assert "function isChatScrollNearBottom" in shared
    assert "const force = Boolean(options.force);" in scroll_helper
    assert "state.shouldStick = isChatScrollNearBottom(scrollNode);" in scroll_helper
    assert "if (!state.shouldStick) return;" in scroll_helper
    assert "scrollNode.scrollTo({ top, behavior });" in scroll_helper
    assert "forceScroll: true" in shared



def test_newsroom_chat_input_clears_immediately_after_submit():
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    submit_handler = web.split('document.querySelector("#chatForm")?.addEventListener("submit", async (event) => {', 1)[1].split('document.querySelector("#taskForm")', 1)[0]
    clear_index = submit_handler.index('input.value = "";')
    send_index = submit_handler.index('await handleAssistantInput(message);')
    assert clear_index < send_index
    assert 'input.dispatchEvent(new Event("input", { bubbles: true }))' in submit_handler


def test_newsroom_evidence_sources_are_collapsed_by_default():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert '<details class="evidence-strip" data-evidence-strip>' in home
    assert 'target.open = false' in web
    assert '<summary>' in web
    assert '证据来源' in web
    assert 'class="evidence-list"' in web
    assert '.analysis-board .evidence-strip > summary' in styles
    assert '.analysis-board .evidence-list' in styles
    summary = styles.split('.analysis-board .evidence-strip > summary', 1)[1].split('}', 1)[0]
    assert 'cursor: pointer' in summary
    assert 'min-height: 44px' in summary



def test_newsroom_web_search_toggle_checked_state_is_blue():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".composer-dock .web-search-toggle input" in styles
    off_state = styles.split(".composer-dock .web-search-toggle input {", 1)[1].split("}", 1)[0]
    assert "background: #cbd5e1" in off_state
    checked_state = styles.split(".composer-dock .web-search-toggle input:checked", 1)[1].split("}", 1)[0]
    assert "background: var(--accent)" in checked_state
    assert "#cbd5e1" not in checked_state
    assert ".composer-dock .web-search-toggle input:checked::after" in styles


def test_newsroom_primary_actions_share_flat_light_blue():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert "--accent: #3d73d6" in styles
    assert "--accent-hover: #3568c4" in styles
    assert "--accent-bright" not in styles
    assert "linear-gradient" not in styles
    assert "radial-gradient" not in styles
    for selector in (
        ".newsroom-topbar .top-actions #refresh",
        ".newsroom-shell .prompt-suggestions .quick-action.primary",
        ".composer-dock .dialog-command button",
        ".newsroom-shell .round-add",
        ".newsroom-shell .chat-user .chat-bubble",
    ):
        assert selector in styles
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "background: var(--accent)" in block
        assert "linear-gradient" not in block



def test_newsroom_slash_command_menu_typography_matches_body_scale():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    strong = styles.split(".newsroom-shell .slash-command-copy strong", 1)[1].split("}", 1)[0]
    assert "font-size: var(--type-body)" in strong
    assert "font-weight: 620" in strong
    assert "letter-spacing" not in strong
    copy = styles.split(".newsroom-shell .slash-command-copy", 1)[1].split("}", 1)[0]
    assert "gap: 2px" in copy
    assert ".newsroom-shell .slash-command-copy span" in styles
    assert "font-size: var(--type-control)" in styles
    assert "font-size: var(--type-meta)" in styles


def test_newsroom_slash_command_menu_uses_light_editorial_popover():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-shell .slash-command-menu" in styles
    menu = styles.split(".newsroom-shell .slash-command-menu", 1)[1].split("}", 1)[0]
    assert "background: rgba(255, 255, 255, 0.96)" in menu
    assert "border-radius: 16px" in menu
    assert "width: min(100%, 720px)" in menu
    assert "max-height: min(330px, 46vh)" in menu
    active = styles.split(".newsroom-shell .slash-command-item.active", 1)[1].split("}", 1)[0]
    assert "background: var(--accent-soft)" in active
    assert "color: var(--ink)" in active
    assert "background: var(--accent)" not in active


def test_newsroom_config_dialog_scroll_and_dark_surface_are_contained():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    dialog = styles.split(".newsroom-shell .config-dialog {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in dialog
    assert "display: flex" in dialog
    assert "padding: 0" in dialog
    assert "max-height: min(760px, calc(100dvh - 40px))" in dialog
    content = styles.split(".newsroom-shell .config-dialog #onboardingForm", 1)[1].split("}", 1)[0]
    assert "flex: 1 1 auto" in content
    assert "overflow-y: auto" in content
    assert "overflow-x: hidden" in content
    assert "background: transparent" in content
    assert ".newsroom-shell .config-dialog .prompt-preview" in styles
    assert "max-height: 150px" in styles
    assert "overflow: auto" in styles
    assert ".newsroom-shell.console-dark .config-dialog" in styles
    dark_dialog = styles.split(".newsroom-shell.console-dark .config-dialog {", 1)[1].split("}", 1)[0]
    assert "background: rgba(17,24,39,.97)" in dark_dialog
    assert "overflow: hidden" in dark_dialog
    dark_preview = styles.split(".newsroom-shell.console-dark .config-dialog .prompt-preview", 1)[1].split("}", 1)[0]
    assert "background: #131c2d" in dark_preview


def test_newsroom_light_slash_command_menu_stays_white_not_button_blue():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-shell.console-light .slash-command-menu" in styles
    light_menu = styles.split(".newsroom-shell.console-light .slash-command-menu", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in light_menu
    assert ".newsroom-shell.console-light .slash-command-item" in styles
    light_item = styles.split(".newsroom-shell.console-light .slash-command-item", 1)[1].split("}", 1)[0]
    assert "background: #ffffff !important" in light_item
    assert "color: var(--ink) !important" in light_item
    assert "linear-gradient" not in light_item
    light_active = styles.split(".newsroom-shell.console-light .slash-command-item:hover", 1)[1].split("}", 1)[0]
    assert "background: #ffffff !important" in light_active
    assert "var(--accent-soft)" not in light_active



def test_newsroom_removes_explanatory_microcopy_from_rails():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    assert 'class="refresh-note"' not in home
    assert "正在更新热点" not in home
    assert "汇总 · 持续观察" not in web
    assert "热点持续观察中" not in web
    assert "围绕当前专题持续更新" not in home
    assert '<header class="rail-title"><h2>跟踪与关联</h2></header>' in home
    assert ".newsroom-left .refresh-note" not in styles
    assert ".rail-title p" not in styles


def test_chat_console_uses_harness_trace_compact_controls_and_latest_message_layout():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    newsroom_styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    assert "legacy-agent-drawer" not in home
    assert 'class="analysis-board"' in home
    assert 'class="workspace-tools"' not in home
    assert ".workspace-tools-popover" not in newsroom_styles
    assert ".messages.chat-stream > .chat-turn:first-child" in styles
    assert "margin-top: auto;" in styles
    assert "grid-template-columns: auto 168px" in styles
    assert ".console-shell .turn-actions button" in styles
    turn_actions = shared.split("function turnActionsHtml", 1)[1].split("document.addEventListener", 1)[0]
    assert "mark-related" not in turn_actions
    assert "mark-unrelated" not in turn_actions
    assert "重新编辑" in turn_actions
    assert 'data-action="changes"' in home
    assert 'data-action="deep-dive-chat"' in home
    assert 'data-action="make-task"' not in home
    assert "conversation-command-bar" in home
    assert "topic-card-skeleton" in home
    assert 'aria-busy="true"' in home
    assert 'target.setAttribute("aria-busy", "false")' in web
    assert 'target.setAttribute("aria-label", "关注专题")' in web
    assert 'target.dataset.loaded = "true"' in web
    assert 'target.dataset.recommendationsLoading = "true"' in web
    assert "const TOPIC_REFRESH_INTERVAL_MS = 180_000" in web
    assert "TOPIC_RECOMMENDATION_RETENTION_MS = 30 * 60_000" in web
    assert "stabilizeRecommendedTopics" in web
    assert "单源待确认" in web
    assert "startTopicAutoRefresh()" in web
    assert 'loadTopics({ quiet: true })' in web
    assert 'target.addEventListener("click", async (event) =>' in web
    assert "await selectTopicCard(button)" in web
    assert "当前对话主题已确定" not in web.split("function bindTopicCards", 1)[1].split("function mergeTopics", 1)[0]
    assert "data-topic-refresh-status" not in home
    assert "grid-template-rows: auto auto minmax(0, 1fr) auto auto" in styles
    assert "本轮过程" in shared
    assert "assistantIdentityHtml" in shared
    assert "mountAssistantSections" in shared
    assert ".assistant-markdown > .answer-section" in styles
    assert "mergePublicExecutionTrace" in shared
    assert "upsertResearchTrace" in shared
    assert 'role="listitem"' in shared
    assert "codeLines.join" in shared
    assert 'html += "<ol>"' in shared
    assert "static/vendor/mermaid.min.js" in home
    assert "mountMermaidDiagrams" in shared
    assert "openMermaidViewer" in shared
    assert "mermaidGraphContext" in shared
    assert "data-mermaid-action=\"factcheck\"" in shared
    assert ".mermaid-viewer-dialog" in styles
    assert ".factcheck-workbench" in styles
    assert 'name: "map"' in shared
    assert "生成事件图谱" in shared
    assert 'language.toLowerCase() === "mermaid"' in shared
    assert "function sanitizeMermaidSource" in shared
    assert "normalizeRelatedResearchSteps" in shared
    assert "这是可核验的执行记录，不是隐藏思维链" in shared
    assert ".related-research-path" in styles
    assert "未公布举办地" not in shared
    assert "click\\s+" in shared
    assert ".chat-mermaid-canvas" in styles
    assert "本地新闻引擎" not in home
    assert "外部搜索工具" not in home
    assert "ES --" not in home
    assert "MySQL --" not in home


def test_chat_web_search_is_enabled_by_default_but_remains_user_controllable():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")

    assert "savedWebSearchPreference === null ? true" in shared
    assert "localStorage.setItem(webSearchPreferenceKey()" in shared
    assert home.count("data-web-search-toggle checked") == 2


def test_chat_model_selector_is_logical_and_sent_with_each_chat_request():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    mobile = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")

    assert home.count("data-chat-model-select") == 2
    assert "元融大模型" in home
    assert "Qwen 3.6" in home
    assert "DeepSeek V4 Flash" in home
    assert 'const DEFAULT_CHAT_MODEL_KEY = "yuanrong-personal-assistant"' in shared
    assert "model_key: chatContext.model_key || getChatModelKey()" in shared
    assert "provider_model" not in shared.split("async function loadOnboardingOptions", 1)[1].split("async function loadProfileIntoForm", 1)[0]
    assert "model_key: getChatModelKey()" in web
    assert "model_key: getChatModelKey()" in mobile


def test_first_run_onboarding_opens_automatically_and_topics_come_from_real_state():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    mobile = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")

    assert "data-onboarding-intro" in home
    assert "initializeUserProfileState()" in web
    assert "data.profile?.onboarding_completed" in web
    assert "dialog.showModal()" in web
    assert 'dataset.firstRun === "true"' in web
    assert "initializeMobileProfileState()" in mobile
    assert "showOnboardingForm()" in mobile
    assert "const disabled = item.implemented" in shared
    assert "aria-disabled" in shared

    web_bootstrap = web.split("const bootstrapTopics = [", 1)[1].split("];", 1)[0]
    mobile_bootstrap = mobile.split("const mobileBootstrapTopics = [", 1)[1].split("];", 1)[0]
    assert 'topic_type: "user"' not in web_bootstrap
    assert 'topic_type: "user"' not in mobile_bootstrap
    assert web_bootstrap.count('topic_type: "system"') == 2
    assert mobile_bootstrap.count('topic_type: "system"') == 2
    assert "composeTopicItems(persisted, stableRecommendations)" in web
    assert "return [...userTopics, ...recommended, ...systemTopics, ...bootstrapTopics]" in web
    assert "composeMobileTopicItems(persisted, recommended.items || [])" in mobile
    assert "return [...userTopics, ...recommended, ...systemTopics, ...mobileBootstrapTopics]" in mobile
    assert "展示 CC 如何" not in shared
    assert "展示 Agent 如何锚定语境" in shared
