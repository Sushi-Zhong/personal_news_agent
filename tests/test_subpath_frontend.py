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
    assert "home.js?v=20260814-ui-fix-3" in home
    assert "shared.js?v=20260810-phone-controls-2" in auth
    assert "ensureRegistrationChallenge(form, status)" in auth_script
    assert "ensureRegistrationChallenge(form, status)" in mobile_script
    assert "function ensureRegistrationChallenge" in source


def test_recommended_topic_cache_replaces_by_event_key_or_article_overlap():
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    mobile = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")

    assert "pna_recommended_topics_v3" in web
    assert "sameRecommendedEvent" in web
    assert "existing.event_key === incoming.event_key" in web
    assert "articleIdsOverlap(existing.article_ids, incoming.article_ids)" in web
    assert "sameRecommendedEvent" in mobile
    assert "existing.event_key === incoming.event_key" in mobile
    assert "articleIdsOverlap(existing.article_ids, incoming.article_ids)" in mobile


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
    styles = (
        (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        + "\n"
        + (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    )

    assert 'class="newsroom-grid news-console"' in home
    assert 'class="newsroom-left"' in home
    assert 'class="newsroom-center"' in home
    assert 'class="newsroom-right"' in home
    assert 'id="themeToggle"' in home
    assert 'aria-label="切换深色背景"' in home
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

    assert 'static/newsroom.css?v=20260814-ui-fix-3' in home
    assert '20260814-ui-fix-3' in app
    assert 'data-body-class="web-shell console-shell newsroom-shell"' in home
    assert 'data-body-class="mobile-shell newsroom-mobile"' in home
    assert 'class="newsroom-left"' in home
    assert 'class="newsroom-center"' in home
    assert 'class="newsroom-right"' in home
    assert 'class="analysis-board"' in home
    assert 'data-current-category-chip' not in home
    assert 'data-current-view-chip' not in home
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


def test_newsroom_refresh_fix_bumps_dynamic_asset_version():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    app = (STATIC_DIR / "home.js").read_text(encoding="utf-8")

    expected_version = "20260814-ui-fix-3"
    assert f"home.js?v={expected_version}" in home
    assert f"newsroom.css?v={expected_version}" in home
    assert expected_version in app
    assert "20260814-skill-flow-1" not in app


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
    assert "bottom: 0" in composer
    assert "backdrop-filter: blur(22px) saturate(150%)" in composer
    assert "calc(var(--newsroom-reading-gutter) * -1)" not in composer
    dialog = styles.split(".newsroom-shell .main-dialog {", 1)[1].split("}", 1)[0]
    assert "--composer-bottom-space: 18px" in dialog
    assert "var(--composer-bottom-space)" in dialog
    assert ".newsroom-shell .assistant-markdown" in styles
    assert "font-size: var(--type-body)" in styles
    assert ".newsroom-shell.console-light .assistant-markdown blockquote" in styles
    light_markdown = styles.split(".newsroom-shell.console-light .assistant-markdown,", 1)[1].split("}", 1)[0]
    assert "color: var(--ink)" in light_markdown
    dialog = styles.split(".composer-dock .dialog-command {", 1)[1].split("}", 1)[0]
    assert "padding: 4px" in dialog
    assert "border-radius: 12px" in dialog
    assert ".composer-dock .dialog-command input" in styles
    input_rules = styles.split(".composer-dock .dialog-command input {", 1)[1].split("}", 1)[0]
    assert "min-height: 34px" in input_rules
    button_rules = styles.split(".composer-dock .dialog-command > .send-arrow {", 1)[1].split("}", 1)[0]
    assert "min-height: 32px" in button_rules
    assert "min-width: 32px" in button_rules


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
        ".composer-dock .dialog-command > .send-arrow",
        ".newsroom-shell .round-add",
        ".newsroom-shell .chat-user .chat-bubble",
    ):
        assert selector in styles
        block = styles.split(selector, 1)[1].split("}", 1)[0]
        assert "background: var(--accent)" in block
        assert "linear-gradient" not in block


def test_newsroom_header_actions_use_restrained_icons_instead_of_text_labels():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    app = (STATIC_DIR / "home.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'id="newTopicConversation"' in home
    assert 'aria-label="新增关注"' in home
    assert '<span aria-hidden="true">+</span>' in home
    assert '>新增</button>' not in home
    for control in ("themeToggle", "openConfig", "refresh"):
        button = home.split(f'id="{control}"', 1)[1].split("</button>", 1)[0]
        assert "action-icon" in button
        assert "<svg" in button
    assert ">个人配置</button>" not in home
    assert ">刷新资讯</button>" not in home
    assert 'button.querySelector(".action-icon")' in app
    assert 'data-theme-icon="moon"' in home
    assert 'data-theme-icon="sun"' in home
    icon_button = styles.split(".newsroom-topbar .top-actions button {", 1)[1].split("}", 1)[0]
    assert "width: 36px" in icon_button
    assert "padding: 0" in icon_button
    assert ".newsroom-topbar .action-icon" in styles
    action_icon = styles.split(".newsroom-topbar .action-icon {", 1)[1].split("}", 1)[0]
    assert "display: inline-flex" in action_icon
    assert "align-items: center" in action_icon
    assert "justify-content: center" in action_icon
    svg = styles.split(".newsroom-topbar .action-icon svg {", 1)[1].split("}", 1)[0]
    assert "display: block" in svg
    assert "stroke: currentColor" in svg
    round_add = styles.split(".newsroom-shell .round-add,", 1)[1].split("}", 1)[0]
    assert "display: inline-flex" in round_add
    assert "align-items: center" in round_add
    assert "justify-content: center" in round_add



def test_newsroom_slash_command_menu_typography_matches_body_scale():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    strong = styles.split(".newsroom-shell .slash-command-copy strong", 1)[1].split("}", 1)[0]
    assert "font-size: var(--type-control)" in strong
    assert "font-weight: 620" in strong
    assert "letter-spacing" not in strong
    copy = styles.split(".newsroom-shell .slash-command-copy", 1)[1].split("}", 1)[0]
    assert "gap: 1px" in copy
    assert ".newsroom-shell .slash-command-copy span" in styles
    assert "font-size: var(--type-caption)" in styles
    item = styles.split(".newsroom-shell .slash-command-item", 1)[1].split("}", 1)[0]
    assert "min-height: 38px" in item
    assert "padding: 5px 8px" in item
    assert "border-radius: 10px" in item


def test_newsroom_slash_command_menu_uses_light_editorial_popover():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".newsroom-shell .slash-command-menu" in styles
    menu = styles.split(".newsroom-shell .slash-command-menu", 1)[1].split("}", 1)[0]
    assert "background: rgba(255, 255, 255, 0.96)" in menu
    assert "border-radius: 16px" in menu
    assert "width: min(100%, 720px)" in menu
    assert "max-height: min(330px, 46vh)" in menu
    assert "scrollbar-width: none" in menu
    active = styles.split(".newsroom-shell .slash-command-item.active", 1)[1].split("}", 1)[0]
    assert "background: var(--accent-soft)" in active
    assert "color: var(--ink)" in active
    assert "background: var(--accent)" not in active
    assert ".newsroom-shell .slash-command-menu::-webkit-scrollbar" in styles


def test_newsroom_slash_command_menu_does_not_preselect_first_item():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert "let activeIndex = -1" in shared
    assert "activeIndex === index" in shared
    assert "aria-selected=\"${activeIndex === index}\"" in shared
    assert "if (activeIndex < 0) activeIndex = 0" in shared
    assert "if (activeIndex < 0) return" in shared
    light_active = styles.split(".newsroom-shell.console-light .slash-command-item:hover", 1)[1].split("}", 1)[0]
    assert "box-shadow: none !important" in light_active
    assert "background: #f5f7fb !important" in light_active


def test_newsroom_skill_command_submit_keeps_auto_scroll_sticky():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    mobile = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")

    select_command = shared.split("const selectCommand = (command) => {", 1)[1].split("};", 1)[0]
    assert 'input.dispatchEvent(new Event("input", { bubbles: true }))' in select_command
    send_into_turn = shared.split("async function sendChatIntoTurn", 1)[1].split("function focusFromChatMessage", 1)[0]
    assert 'scrollChatToBottom(targetNode, "auto", { force: true });' in send_into_turn
    assert "const streamed = await streamChat(payload, assistantNode, targetNode);" in send_into_turn
    keydown_handler = shared.split('input.addEventListener("keydown", (event) => {', 1)[1].split('menu.addEventListener("mousedown"', 1)[0]
    enter_handler = keydown_handler.split('} else if (event.key === "Enter" || event.key === "Tab") {', 1)[1].split('} else if (event.key === "Escape")', 1)[0]
    assert 'if (activeIndex < 0) return;\n      event.preventDefault();' in enter_handler
    assert 'event.preventDefault();\n      if (activeIndex < 0) return;' not in enter_handler
    mobile_submit = mobile.split('document.querySelector("#chatForm").addEventListener("submit", async (event) => {', 1)[1].split('document.querySelector("#editProfileMobile")', 1)[0]
    mobile_clear_index = mobile_submit.index('input.value = "";')
    mobile_send_index = mobile_submit.index("await handleMobileAssistantInput(message);")
    assert mobile_clear_index < mobile_send_index
    assert 'input.dispatchEvent(new Event("input", { bubbles: true }))' in mobile_submit


def test_newsroom_config_dialog_scroll_and_dark_surface_are_contained():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    dialog = styles.split(".newsroom-shell .config-dialog {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in dialog
    assert "display: flex" in dialog
    assert "padding: 0" in dialog
    assert "max-height: min(700px, calc(100dvh - 32px))" in dialog
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


def test_newsroom_config_dialog_close_action_is_explicit_and_reachable():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'id="configDialog"' in home
    assert 'data-close-config' in home
    close_button = home.split('data-close-config', 1)[1].split("</button>", 1)[0]
    assert 'aria-label="关闭个人配置"' in close_button
    assert "<svg" in close_button
    assert "关闭</button>" not in close_button
    assert "showModal()" in web
    assert 'data-close-config' in web
    assert "dialog.close()" in web
    assert ".newsroom-shell .config-dialog:not([open])" in styles
    hidden_dialog = styles.split(".newsroom-shell .config-dialog:not([open])", 1)[1].split("}", 1)[0]
    assert "display: none" in hidden_dialog


def test_newsroom_config_dialog_has_persistent_save_action():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    dialog = home.split('id="configDialog"', 1)[1].split("</dialog>", 1)[0]
    form = dialog.split('id="onboardingForm"', 1)[1].split("</form>", 1)[0]
    assert "初始化/保存配置" not in form
    assert 'class="config-dialog-footer"' in dialog
    assert 'type="submit" form="onboardingForm"' in dialog
    assert ">保存<" in dialog
    footer = styles.split(".newsroom-shell .config-dialog .config-dialog-footer {", 1)[1].split("}", 1)[0]
    assert "flex: 0 0 auto" in footer
    assert "background:" in footer


def test_newsroom_light_event_popover_uses_light_surface():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    popover = styles.split(".newsroom-shell.console-light .event-action-popover {", 1)[1].split("}", 1)[0]
    pointer = styles.split(".newsroom-shell.console-light .event-action-popover::before {", 1)[1].split("}", 1)[0]
    copy = styles.split(".newsroom-shell.console-light .event-action-popover p {", 1)[1].split("}", 1)[0]
    primary = styles.split(".newsroom-shell.console-light .event-action-popover button:first-child {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in popover
    assert "color: var(--ink)" in popover
    assert "background: #ffffff" in pointer
    assert "color: var(--ink-soft)" in copy
    assert "background: var(--accent)" in primary


def test_newsroom_event_popover_uses_shared_typography_scale():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    surface = styles.split(".newsroom-shell .event-action-popover {", 1)[1].split("}", 1)[0]
    title = styles.split(".newsroom-shell .event-action-popover strong {", 1)[1].split("}", 1)[0]
    copy = styles.split(".newsroom-shell .event-action-popover p {", 1)[1].split("}", 1)[0]
    button = styles.split(".newsroom-shell .event-action-popover button {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--font-newsroom)" in surface
    assert "font-size: var(--type-body)" in title
    assert "font-size: var(--type-meta)" in copy
    assert "font-size: var(--type-control)" in button
    assert "font-weight: 560" in button
    assert "min-height: 34px" in button


def test_newsroom_config_dialog_controls_are_compact_not_roomy():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    dialog = styles.split(".newsroom-shell .config-dialog {", 1)[1].split("}", 1)[0]
    assert "width: min(640px, calc(100vw - 32px))" in dialog
    assert "max-height: min(700px, calc(100dvh - 32px))" in dialog
    head = styles.split(".newsroom-shell .config-dialog .dialog-head {", 1)[1].split("}", 1)[0]
    assert "padding: 16px 20px 12px" in head
    field = styles.split(".newsroom-shell .config-dialog input,", 1)[1].split("}", 1)[0]
    assert "min-height: 38px" in field
    assert "border-radius: 10px" in field
    textarea = styles.split(".newsroom-shell .config-dialog textarea {", 1)[1].split("}", 1)[0]
    assert "min-height: 96px" in textarea
    assert "max-height: 150px" in textarea
    label = styles.split(".newsroom-shell .config-dialog .choice-grid label {", 1)[1].split("}", 1)[0]
    assert "min-height: 30px" in label
    assert "font-size: var(--type-control)" in label
    checkbox = styles.split('.newsroom-shell .config-dialog .choice-grid input[type="checkbox"]', 1)[1].split("}", 1)[0]
    assert "width: 18px" in checkbox
    assert "height: 18px" in checkbox


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
    assert "background: #f5f7fb !important" in light_active
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


def test_newsroom_refresh_keeps_topic_shell_synced_with_restored_conversation():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")

    center = home.split('<section class="newsroom-center">', 1)[1].split('<aside class="newsroom-right">', 1)[0]
    assert "新能源汽车产业链" not in center
    assert "<h2 data-agent-topic>新对话</h2>" in center
    assert "<h2 data-topic-heading>新对话</h2>" in center
    assert 'input name="topic" value=""' in home
    assert "function syncTopicShell" in web
    assert "syncTopicShell();" in web.split("async function loadTopicView", 1)[1].split("if (!consoleState.topic)", 1)[0]
    assert "const requestedTopic = consoleState.topic;" in web
    assert "if (requestedTopic !== consoleState.topic) return;" in web
    assert "function applyChatConversationContext(context = {}, options = {})" in web
    assert "&& !options.forceTopic" in web
    assert "options.reloadTopicView && previousTopic !== consoleState.topic" in web
    assert "window.applyChatConversationContext(data.context || {}, { forceTopic: true, reloadTopicView: true })" in shared
    bootstrap = web.split("const consoleState =", 1)[0]
    assert "conversationId = null" not in bootstrap
    assert 'localStorage.removeItem("pna_conversation_id")' not in bootstrap
    assert "async function initializeNewsroom()" in web
    initializer = web.split("async function initializeNewsroom()", 1)[1].split("async function initializeUserProfileState", 1)[0]
    assert "await restoreChatMemory(\"#messages\");" in initializer
    assert "await refreshWeb();" in initializer
    assert initializer.index('await restoreChatMemory("#messages");') < initializer.index("await refreshWeb();")
    assert 'restoreChatMemory("#messages");' not in web.split("async function initializeNewsroom()", 1)[0]


def test_new_conversation_clears_topic_presentation_on_desktop_and_mobile():
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    mobile = (STATIC_DIR / "mobile.js").read_text(encoding="utf-8")

    desktop_reset = web.split("function startNewTopicConversation()", 1)[1].split("async function loadTasks", 1)[0]
    mobile_reset = mobile.split("async function startNewMobileTopicConversation()", 1)[1].split("function mergeMobileTopics", 1)[0]
    assert "loadTopicView();" in desktop_reset
    assert "renderMobileResponseScopedFeed([]);" in mobile_reset


def test_newsroom_refresh_loads_topic_view_even_when_sidebar_requests_fail():
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")

    refresh = web.split("async function refreshWeb()", 1)[1].split("async function handleAssistantInput", 1)[0]
    assert "Promise.allSettled" in refresh
    assert "const refreshResults =" in refresh
    assert "await loadTopicView();" in refresh
    assert refresh.index("Promise.allSettled") < refresh.index("await loadTopicView();")
    assert "refreshResults.some((result) => result.status === \"rejected\")" in refresh
    assert "部分数据稍后重试" in refresh
    assert "Promise.all([" not in refresh


def test_newsroom_topic_view_failure_clears_loading_placeholders():
    web = (STATIC_DIR / "web.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    load_topic = web.split("async function loadTopicView()", 1)[1].split("async function runNativeIngest", 1)[0]
    catch_block = load_topic.split("} catch (error) {", 1)[1].split("\n  }", 1)[0]
    assert "renderTopicLoadError(error, requestedTopic);" in catch_block
    assert "function renderTopicLoadError" in web
    error_renderer = web.split("function renderTopicLoadError", 1)[1].split("function renderTopicHeader", 1)[0]
    assert "[data-topic-summary]" in error_renderer
    assert "专题内容暂时无法显示" in error_renderer
    assert "[data-topic-article-count]" in error_renderer
    assert "[data-topic-event-count]" in error_renderer
    assert "[data-topic-node-count]" in error_renderer
    assert ".topic-load-error" in styles
    error_style = styles.split(".analysis-board .topic-load-error", 1)[1].split("}", 1)[0]
    assert "color: var(--ink-soft)" in error_style
    empty_style = styles.split(".analysis-board .topic-visual .empty-state", 1)[1].split("}", 1)[0]
    assert "color: var(--ink-soft)" in empty_style


def test_newsroom_sent_message_and_send_control_use_compact_capsules():
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    user_turn = styles.split(".newsroom-shell .chat-user {", 1)[1].split("}", 1)[0]
    assert "max-width: min(68%, 560px)" in user_turn
    user_bubble = styles.split(".newsroom-shell .chat-user .chat-bubble {", 1)[1].split("}", 1)[0]
    assert "padding: 9px 14px" in user_bubble
    assert "border-radius: 15px 15px 5px 15px" in user_bubble
    assert ".composer-dock .dialog-command button {" not in styles
    send_button = styles.split(".composer-dock .dialog-command > .send-arrow {", 1)[1].split("}", 1)[0]
    assert 'class="send-arrow"' in home
    assert 'aria-label="发送消息"' in home
    assert '<span aria-hidden="true">↑</span>' in home
    assert ">发送</button>" not in home
    assert "width: 32px" in send_button
    assert "height: 32px" in send_button
    assert "padding: 0" in send_button
    assert "border-radius: 999px" in send_button


def test_newsroom_send_button_rules_do_not_collapse_slash_command_items():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert ".composer-dock .dialog-command > .send-arrow" in styles
    assert ".composer-dock .dialog-command button" not in styles
    item = styles.split(".newsroom-shell .slash-command-item {", 1)[1].split("}", 1)[0]
    assert "display: grid" in item
    assert "width: 100%" in item


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


def test_newsroom_center_keeps_a_continuous_surface_behind_composer_tail():
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    center = styles.split(".newsroom-center {", 1)[1].split("}", 1)[0]
    assert "padding: 24px var(--newsroom-gap) 0" in center
    dialog = styles.split(".newsroom-shell .main-dialog {", 1)[1].split("}", 1)[0]
    assert "min-height: 100%" in dialog
    assert "box-sizing: border-box" in dialog
    assert "border-bottom-left-radius: 0" in dialog
    assert "border-bottom-right-radius: 0" in dialog


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
    assert 'request("/api/skills")' in shared
    assert 'outputKind: item.output_kind || "default_markdown"' in shared
    assert "label: item.name || item.description || item.id" in shared
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


def test_newsroom_mermaid_graph_adapts_to_light_theme():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert "function currentMermaidTheme" in shared
    assert 'document.body.classList.contains("console-dark") ? "dark" : "base"' in shared
    assert 'theme: "dark"' not in shared
    assert 'document.addEventListener("pna:themechange"' in shared
    assert ".newsroom-shell.console-light .chat-mermaid" in styles
    light_graph = styles.split(".newsroom-shell.console-light .chat-mermaid {", 1)[1].split("}", 1)[0]
    assert "background: #ffffff" in light_graph
    assert "border-color: #dbe7f6" in light_graph
    assert ".newsroom-shell.console-light .mermaid-viewer-dialog" in styles
    assert "EVENT GRAPH" not in shared


def test_newsroom_mermaid_viewer_has_restrained_zoom_and_drag_controls():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")

    assert 'class="mermaid-viewer-stage"' in shared
    assert 'class="mermaid-viewer-controls"' in shared
    assert 'data-mermaid-zoom="out"' in shared
    assert 'data-mermaid-zoom="in"' in shared
    assert 'data-mermaid-zoom="reset"' in shared
    assert 'data-mermaid-zoom-value' in shared
    assert "function bindMermaidPanZoom" in shared
    assert "function fitMermaidToStage" in shared
    assert "getBoundingClientRect" in shared
    assert "applyMermaidTransform" in shared
    assert "state.fitScale" in shared
    assert "pointerdown" in shared
    assert "wheel" in shared
    assert "dblclick" in shared
    assert ".mermaid-viewer-controls" in styles
    controls = styles.split(".mermaid-viewer-controls", 1)[1].split("}", 1)[0]
    assert "position: absolute" in controls
    assert "background: rgba(255, 255, 255, 0.86)" in controls
    assert ".newsroom-shell.console-dark .mermaid-viewer-controls" in styles
    assert ".newsroom-shell.console-light .mermaid-viewer-controls button" in styles
    light_buttons = styles.split(".newsroom-shell.console-light .mermaid-viewer-controls button", 1)[1].split("}", 1)[0]
    assert "color: var(--ink) !important" in light_buttons
    assert "background: #ffffff !important" in light_buttons


def test_newsroom_mermaid_viewer_fills_stage_and_uses_node_click_selection():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")
    styles = (
        (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        + "\n"
        + (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")
    )

    assert ".mermaid-viewer-shell" in styles
    shell = styles.split(".mermaid-viewer-shell {", 1)[1].split("}", 1)[0]
    assert "grid-template-rows: auto minmax(0, 1fr)" in shell
    assert ".mermaid-viewer-body" in styles
    body = styles.split(".mermaid-viewer-body {", 1)[1].split("}", 1)[0]
    assert "min-height: 0" in body
    assert ".mermaid-viewer-stage" in styles
    stage = styles.split(".mermaid-viewer-stage {", 1)[1].split("}", 1)[0]
    assert "display: flex" in stage
    assert "align-items: center" in styles
    assert "justify-content: center" in styles
    assert ".mermaid-viewer-canvas" in styles
    assert "width: 100%" in styles
    assert "height: 100%" in styles
    assert "min-height: 0" in styles
    assert "addEventListener(\"pointerdown\"" in shared
    assert "data-mermaid-node" in shared
    assert '"[data-mermaid-node]"' in shared
    assert "selectMermaidNode" in shared
    assert "selectNode(event.target.closest?.(\".node\"))" not in shared


def test_newsroom_mermaid_viewer_zoom_keeps_svg_vector_crisp():
    shared = (STATIC_DIR / "shared.js").read_text(encoding="utf-8")

    assert "svg.style.width = `${Math.round(stageWidth * state.scale)}px`" in shared
    assert "svg.style.height = `${Math.round(stageHeight * state.scale)}px`" in shared
    assert "svg.style.transform = `translate(" not in shared
    assert "will-change: transform" not in (STATIC_DIR / "newsroom.css").read_text(encoding="utf-8")


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
