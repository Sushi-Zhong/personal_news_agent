from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import secrets
import time
from typing import Any, Awaitable, Callable


class ConfirmationError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True)
class ConfirmationTicket:
    confirmation_id: str
    token: str
    status: str
    expires_at: datetime


@dataclass
class _Record:
    confirmation_id: str
    token_digest: str
    user_id: str
    conversation_id: str
    preview: dict[str, Any]
    preview_digest: str
    task_payload: dict[str, Any]
    created_at: datetime
    expires_at: datetime
    created_monotonic: float
    source_turn_id: str | None
    status: str = "pending"
    result: dict[str, Any] | None = None


class ScheduleConfirmationService:
    def __init__(
        self,
        *,
        ttl_seconds: int = 900,
        max_records: int = 1000,
        utcnow: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_records = max_records
        self._utcnow = utcnow or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic or time.monotonic
        self._lock = asyncio.Lock()
        self._records: dict[str, _Record] = {}

    async def issue(
        self,
        *,
        user_id: str,
        conversation_id: str,
        preview: dict[str, Any],
        task_payload: dict[str, Any],
        source_turn_id: str | None = None,
    ) -> ConfirmationTicket:
        token = secrets.token_urlsafe(32)
        token_digest = _digest_text(token)
        now = self._utcnow()
        monotonic_now = self._monotonic()
        record = _Record(
            confirmation_id=f"scf_{secrets.token_hex(12)}",
            token_digest=token_digest,
            user_id=user_id,
            conversation_id=conversation_id,
            preview=_copy_json(preview),
            preview_digest=_digest_json(preview),
            task_payload=_copy_json(task_payload),
            created_at=now,
            expires_at=now + timedelta(seconds=self.ttl_seconds),
            created_monotonic=monotonic_now,
            source_turn_id=source_turn_id,
        )
        async with self._lock:
            self._cleanup_locked(now, monotonic_now)
            if len(self._records) >= self.max_records:
                raise ConfirmationError(503, "confirmation_capacity", "确认服务繁忙，请稍后重试。")
            self._records[token_digest] = record
        return ConfirmationTicket(
            confirmation_id=record.confirmation_id,
            token=token,
            status="pending",
            expires_at=record.expires_at,
        )

    async def confirm(
        self,
        *,
        user_id: str,
        conversation_id: str,
        confirmation_id: str,
        token: str,
        create: Callable[[dict[str, Any]], Any | Awaitable[Any]],
        preview: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        digest = _digest_text(token)
        async with self._lock:
            record = self._validated_record_locked(
                digest,
                user_id=user_id,
                conversation_id=conversation_id,
                confirmation_id=confirmation_id,
                preview=preview,
            )
            if record.status == "confirmed":
                return {**(record.result or {}), "confirmation_status": "confirmed", "replayed": True}
            if record.status == "consuming":
                raise ConfirmationError(409, "confirmation_consuming", "确认正在处理中，请勿重复点击。")
            if record.status == "cancelled":
                raise ConfirmationError(409, "confirmation_cancelled", "该确认已取消。")
            if record.status != "pending":
                raise ConfirmationError(409, "confirmation_state", "该确认当前不可执行。")
            record.status = "consuming"
            task_payload = _copy_json(record.task_payload)
        try:
            created = create(task_payload)
            if inspect.isawaitable(created):
                created = await created
            result = dict(created or {})
        except Exception:
            async with self._lock:
                current = self._records.get(digest)
                if current is record and current.status == "consuming":
                    current.status = "pending"
            raise
        async with self._lock:
            record.status = "confirmed"
            record.result = result
        return {**result, "confirmation_status": "confirmed", "replayed": False}

    async def cancel(
        self,
        *,
        user_id: str,
        conversation_id: str,
        confirmation_id: str,
        token: str,
    ) -> dict[str, Any]:
        digest = _digest_text(token)
        async with self._lock:
            record = self._validated_record_locked(
                digest,
                user_id=user_id,
                conversation_id=conversation_id,
                confirmation_id=confirmation_id,
            )
            if record.status == "cancelled":
                return {"confirmation_status": "cancelled", "replayed": True}
            if record.status == "confirmed":
                raise ConfirmationError(409, "confirmation_confirmed", "任务已经创建，不能取消确认。")
            if record.status == "consuming":
                raise ConfirmationError(409, "confirmation_consuming", "确认正在处理中，暂时不能取消。")
            record.status = "cancelled"
            return {"confirmation_status": "cancelled", "replayed": False}

    def debug_token_digests(self) -> frozenset[str]:
        return frozenset(self._records)

    def _validated_record_locked(
        self,
        digest: str,
        *,
        user_id: str,
        conversation_id: str,
        confirmation_id: str,
        preview: dict[str, Any] | None = None,
    ) -> _Record:
        record = self._records.get(digest)
        if not record:
            raise ConfirmationError(404, "confirmation_not_found", "确认令牌不存在或服务已重启。")
        if self._expired(record, self._utcnow(), self._monotonic()):
            record.status = "expired"
            raise ConfirmationError(410, "confirmation_expired", "确认已过期，请重新发起。")
        if (
            record.user_id != user_id
            or record.conversation_id != conversation_id
            or record.confirmation_id != confirmation_id
        ):
            raise ConfirmationError(403, "confirmation_binding", "确认令牌与当前用户或会话不匹配。")
        if preview is not None and _digest_json(preview) != record.preview_digest:
            raise ConfirmationError(409, "confirmation_preview_changed", "确认预览与原始快照不一致。")
        return record

    def _cleanup_locked(self, now: datetime, monotonic_now: float) -> None:
        stale = [
            digest
            for digest, record in self._records.items()
            if self._expired(record, now, monotonic_now)
            and monotonic_now - record.created_monotonic > self.ttl_seconds * 2
        ]
        for digest in stale:
            self._records.pop(digest, None)

    def _expired(self, record: _Record, now: datetime, monotonic_now: float) -> bool:
        return now >= record.expires_at or monotonic_now - record.created_monotonic >= self.ttl_seconds


def _digest_text(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _digest_json(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _copy_json(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))
