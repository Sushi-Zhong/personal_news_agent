from pathlib import Path

import pytest

from personal_news_agent.config import Settings
from personal_news_agent.services.auth import AuthError, AuthService
from personal_news_agent.services.phone_verification import PhoneVerificationError
from personal_news_agent.services.store import NewsStore


def _auth(tmp_path: Path, **overrides) -> AuthService:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'phone-auth.db'}",
        seed_demo_data=False,
        phone_challenge_provider="mock",
        phone_challenge_secret="test-phone-challenge-secret-that-is-long-enough",
        phone_challenge_mock_enabled=True,
        phone_challenge_mock_code="123456",
        phone_challenge_resend_seconds=10,
        **overrides,
    )
    store = NewsStore(settings.sqlite_path)
    store.init()
    return AuthService(store, settings)


def test_phone_code_registration_login_and_one_time_consumption(tmp_path: Path) -> None:
    auth = _auth(tmp_path)
    mobile = "13800138000"

    challenge = auth.request_registration_code(mobile, "127.0.0.1")
    assert challenge["mobile_masked"] == "138****8000"
    assert challenge["debug_code"] == "123456"

    with auth.store.connect() as conn:
        stored = conn.execute(
            "SELECT * FROM pna_phone_verification_challenges WHERE challenge_id = ?",
            (challenge["challenge_id"],),
        ).fetchone()
    assert mobile not in tuple(str(value) for value in stored)
    assert "123456" not in tuple(str(value) for value in stored)

    registered = auth.register_phone(
        mobile=mobile,
        challenge_id=challenge["challenge_id"],
        verification_code="123456",
        password="12345678",
        confirm_password="12345678",
    )
    assert registered["user"]["mobile"] == "138****8000"
    assert registered["session"]["token"]

    logged_in = auth.login(mobile, "12345678")
    assert logged_in["user"]["id"] == registered["user"]["id"]

    with pytest.raises(PhoneVerificationError) as consumed:
        auth.phone_verification.verify_code(challenge["challenge_id"], mobile, "123456")
    assert getattr(consumed.value, "code", "") == "phone_code_consumed"

    with pytest.raises(AuthError) as reused:
        auth.register_phone(
            mobile=mobile,
            challenge_id=challenge["challenge_id"],
            verification_code="123456",
            password="12345678",
            confirm_password="12345678",
        )
    assert reused.value.code == "mobile_already_registered"


def test_phone_code_rejects_wrong_code_and_resend_during_cooldown(tmp_path: Path) -> None:
    auth = _auth(tmp_path)
    mobile = "13900139000"
    challenge = auth.request_registration_code(mobile, "127.0.0.2")

    with pytest.raises(AuthError) as wrong:
        auth.register_phone(
            mobile=mobile,
            challenge_id=challenge["challenge_id"],
            verification_code="654321",
            password="abcdefgh",
            confirm_password="abcdefgh",
        )
    assert wrong.value.code == "phone_code_invalid"

    with pytest.raises(AuthError) as cooldown:
        auth.request_registration_code(mobile, "127.0.0.2")
    assert cooldown.value.code == "phone_code_resend_too_soon"
    assert cooldown.value.status_code == 429


def test_phone_registration_requires_configured_provider(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'disabled-auth.db'}",
        seed_demo_data=False,
        phone_challenge_provider="disabled",
        phone_challenge_secret=None,
    )
    store = NewsStore(settings.sqlite_path)
    store.init()
    auth = AuthService(store, settings)

    assert auth.phone_registration_status()["available"] is False
    with pytest.raises(AuthError) as unavailable:
        auth.request_registration_code("13800138000", "127.0.0.1")
    assert unavailable.value.code == "phone_challenge_not_configured"
    assert unavailable.value.status_code == 503


def test_phone_registration_rejects_invalid_mobile_as_public_auth_error(tmp_path: Path) -> None:
    auth = _auth(tmp_path)
    with pytest.raises(AuthError) as invalid:
        auth.register_phone(
            mobile="12800138000",
            challenge_id="pvc_invalid_challenge",
            verification_code="123456",
            password="12345678",
            confirm_password="12345678",
        )
    assert invalid.value.code == "invalid_mobile"
    assert invalid.value.status_code == 400
