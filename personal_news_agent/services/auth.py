from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from personal_news_agent.config import Settings
from personal_news_agent.services.phone_verification import (
    PhoneVerificationError,
    PhoneVerificationService,
    normalize_mainland_mobile,
)
from personal_news_agent.services.realname import RealNameVerificationError, RealNameVerificationService
from personal_news_agent.services.store import NewsStore


class AuthError(ValueError):
    def __init__(self, code: str, message: str | None = None, *, status_code: int = 400) -> None:
        super().__init__(message or code)
        self.code = code
        self.status_code = status_code


class AuthService:
    def __init__(self, store: NewsStore, settings: Settings):
        self.store = store
        self.settings = settings
        self.realname = RealNameVerificationService(settings)
        self.phone_verification = PhoneVerificationService(store, settings)

    def register(self, display_name: str, email: str | None, password: str | None) -> dict:
        password_hash = _hash_password(password) if password else None
        if email and self.store.get_user_by_email(email):
            raise AuthError("email already registered")
        user = self.store.create_user(display_name=display_name, email=email, password_hash=password_hash)
        return self._with_session(user)

    def login(self, email: str, password: str) -> dict:
        identifier = str(email or "").strip()
        user = None
        try:
            user = self.store.get_user_by_mobile(normalize_mainland_mobile(identifier))
        except PhoneVerificationError:
            pass
        user = user or self.store.get_user_by_username(identifier) or self.store.get_user_by_email(identifier)
        stored_password = user.get("password_hash") if user else None
        password_valid = _verify_password(password, stored_password or _DUMMY_PASSWORD_HASH)
        if not user or not stored_password or not password_valid:
            raise AuthError("invalid_credentials", "手机号或密码不正确。", status_code=401)
        public_user = {"id": user["id"], "display_name": user["display_name"], "email": user.get("email"), "username": user.get("username"), "mobile": user.get("mobile")}
        return self._with_session(public_user)

    def phone_registration_status(self) -> dict:
        return self.phone_verification.status()

    def request_registration_code(self, mobile: str, remote_addr: str = "") -> dict:
        try:
            return self.phone_verification.request_code(mobile, remote_addr)
        except PhoneVerificationError as exc:
            error = AuthError(exc.code, str(exc), status_code=exc.status_code)
            error.retry_after_seconds = exc.retry_after_seconds
            raise error from exc

    def register_phone(
        self,
        *,
        mobile: str,
        challenge_id: str,
        verification_code: str,
        password: str,
        confirm_password: str,
    ) -> dict:
        try:
            normalized_mobile = normalize_mainland_mobile(mobile)
        except PhoneVerificationError as exc:
            raise AuthError(exc.code, str(exc), status_code=exc.status_code) from exc
        if len(password) < 8:
            raise AuthError("weak_password", "密码至少需要 8 个字符。")
        if len(password) > 128:
            raise AuthError("invalid_password", "密码不能超过 128 个字符。")
        if password != confirm_password:
            raise AuthError("password_mismatch", "两次输入的密码不一致。")
        if self.store.get_user_by_mobile(normalized_mobile):
            raise AuthError("mobile_already_registered", "该手机号已经注册，请直接登录。", status_code=409)
        try:
            proof = self.phone_verification.verify_code(
                challenge_id,
                normalized_mobile,
                verification_code,
            )
            user = self.store.create_phone_user(
                mobile=normalized_mobile,
                password_hash=_hash_password(password),
                challenge_id=proof.challenge_id,
                mobile_hash=proof.mobile_hash,
                verification_provider=proof.provider,
            )
        except PhoneVerificationError as exc:
            raise AuthError(exc.code, str(exc), status_code=exc.status_code) from exc
        except ValueError as exc:
            if str(exc) == "mobile_already_registered":
                raise AuthError("mobile_already_registered", "该手机号已经注册，请直接登录。", status_code=409) from exc
            if str(exc) == "phone_code_consumed_or_expired":
                raise AuthError("phone_code_consumed", "短信验证码已失效或已使用，请重新获取。") from exc
            raise
        return self._with_session(user)

    def register_with_realname(self, username: str, password: str, confirm_password: str, real_name: str, mobile: str, id_card: str | None = None) -> dict:
        username = username.strip()
        mobile = mobile.strip()
        if len(username) < 3:
            raise AuthError("username must be at least 3 characters")
        if password != confirm_password:
            raise AuthError("password and confirm_password do not match")
        if self.store.get_user_by_username(username):
            raise AuthError("username already registered")
        if self.store.get_user_by_mobile(mobile):
            raise AuthError("mobile already registered")
        try:
            verification = self.realname.verify(real_name=real_name, id_card=id_card, mobile=mobile)
        except RealNameVerificationError as exc:
            raise AuthError(str(exc)) from exc
        if not verification.passed:
            raise AuthError(verification.message)
        user = self.store.create_verified_user(
            username=username,
            password_hash=_hash_password(password),
            real_name=real_name.strip(),
            mobile=mobile,
            id_card_hash=_hash_secret(id_card.upper()) if id_card else None,
            id_card_masked=_mask_id_card(id_card.upper()) if id_card else None,
            verification=verification.__dict__,
        )
        return self._with_session(user)

    def wechat_status(self) -> dict:
        configured = bool(self.settings.wechat_app_id and self.settings.wechat_redirect_uri)
        can_exchange_code = bool(configured and self.settings.wechat_app_secret)
        return {
            "configured": configured,
            "can_exchange_code": can_exchange_code,
            "mode": self.settings.wechat_login_mode,
            "requires_official_app": True,
            "required_env": ["WECHAT_APP_ID", "WECHAT_APP_SECRET", "WECHAT_REDIRECT_URI"],
        }

    def wechat_login_url(self, mode: str | None = None, state: str | None = None, redirect_uri: str | None = None) -> dict:
        if not self.settings.wechat_app_id:
            raise AuthError("WECHAT_APP_ID is not configured")
        redirect = redirect_uri or self.settings.wechat_redirect_uri
        if not redirect:
            raise AuthError("WECHAT_REDIRECT_URI is not configured")
        login_mode = mode or self.settings.wechat_login_mode
        state_value = state or secrets.token_urlsafe(16)
        if login_mode == "mp":
            base = "https://open.weixin.qq.com/connect/oauth2/authorize"
            scope = "snsapi_userinfo"
        else:
            base = "https://open.weixin.qq.com/connect/qrconnect"
            scope = "snsapi_login"
        query = urlencode(
            {
                "appid": self.settings.wechat_app_id,
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": scope,
                "state": state_value,
            }
        )
        return {"url": f"{base}?{query}#wechat_redirect", "state": state_value, "mode": login_mode, "scope": scope}

    async def wechat_callback(self, code: str, state: str | None = None) -> dict:
        if not self.settings.wechat_app_id or not self.settings.wechat_app_secret:
            raise AuthError("WECHAT_APP_ID and WECHAT_APP_SECRET are required to exchange code")
        async with httpx.AsyncClient(timeout=12.0) as client:
            token_resp = await client.get(
                "https://api.weixin.qq.com/sns/oauth2/access_token",
                params={
                    "appid": self.settings.wechat_app_id,
                    "secret": self.settings.wechat_app_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            token_resp.raise_for_status()
            token = token_resp.json()
            if "errcode" in token:
                raise AuthError(f"wechat token exchange failed: {token}")
            openid = token["openid"]
            unionid = token.get("unionid")
            userinfo = {"openid": openid, "unionid": unionid}
            if token.get("access_token"):
                info_resp = await client.get(
                    "https://api.weixin.qq.com/sns/userinfo",
                    params={"access_token": token["access_token"], "openid": openid, "lang": "zh_CN"},
                )
                if info_resp.status_code == 200:
                    candidate = info_resp.json()
                    if "errcode" not in candidate:
                        userinfo.update(candidate)
        user = self.store.upsert_auth_identity(
            provider="wechat",
            provider_user_id=openid,
            display_name=userinfo.get("nickname") or "微信用户",
            union_id=unionid,
            raw=userinfo,
        )
        return {**self._with_session(user), "state": state}

    def _with_session(self, user: dict) -> dict:
        token = secrets.token_urlsafe(32)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        self.store.create_session(user["id"], token, expires_at)
        safe_user = {"id": user["id"], "display_name": user["display_name"], "email": user.get("email"), "username": user.get("username"), "mobile": _mask_mobile(user.get("mobile")) if user.get("mobile") else None}
        return {"user": safe_user, "session": {"token": token, "expires_at": expires_at}}


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return "pbkdf2_sha256$310000$" + base64.b64encode(salt).decode("ascii") + "$" + base64.b64encode(digest).decode("ascii")


def _verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, rounds, salt_b64, digest_b64 = stored.split("$", 3)
        round_count = int(rounds)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= round_count <= 2_000_000:
            return False
        salt = base64.b64decode(salt_b64, validate=True)
        expected = base64.b64decode(digest_b64, validate=True)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, round_count)
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


_DUMMY_PASSWORD_HASH = _hash_password(secrets.token_urlsafe(24))


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask_id_card(value: str) -> str:
    if len(value) < 8:
        return "***"
    return value[:3] + "*" * (len(value) - 7) + value[-4:]


def _mask_mobile(value: str) -> str:
    return value[:3] + "****" + value[-4:] if len(value) == 11 else value
