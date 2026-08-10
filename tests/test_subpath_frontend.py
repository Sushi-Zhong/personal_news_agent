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
    assert "home.js?v=20260810-chat-model-selector-1" in home
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
    assert 'FRONTEND_REVISION = "20260810-chat-model-selector-1"' in routes
    assert '"Cache-Control": "no-store, max-age=0"' in routes
    assert '"frontend_revision": FRONTEND_REVISION' in routes


def test_console_theme_has_dark_drawers_readable_content_and_responsive_rails():
    styles = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
    home = (STATIC_DIR / "home.html").read_text(encoding="utf-8")
    assert "styles.css?v=20260810-chat-model-selector-1" in home
    assert ".console-shell .agent-drawer" in styles
    assert "background: rgba(10, 28, 45, 0.96)" in styles
    assert ".console-shell .assistant-markdown h3" in styles
    assert ".console-shell .chat-event strong" in styles
    assert "@media (max-width: 1380px)" in styles
    assert "position: sticky" in styles


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
