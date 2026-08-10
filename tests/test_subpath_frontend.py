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
    assert "home.js?v=20260810-phone-controls-1" in home
    assert "shared.js?v=20260810-phone-controls-1" in auth
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
