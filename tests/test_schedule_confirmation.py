from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from personal_news_agent.services.schedule_confirmation import (
    ConfirmationError,
    ScheduleConfirmationService,
)
from personal_news_agent.services.tasks import ScheduledTaskService


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 8, 14, 1, 0, tzinfo=timezone.utc)
        self.monotonic = 100.0

    def utcnow(self):
        return self.now

    def monotonic_now(self):
        return self.monotonic

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.monotonic += seconds


def _preview() -> dict:
    return {
        "task_type": "scheduled_push",
        "schedule": "0 9 * * *",
        "timezone": "Asia/Shanghai",
        "next_run_at": "2026-08-15T01:00:00+00:00",
        "topics": ["AI Agent"],
        "category_scope": ["tech"],
        "source_scope": [],
        "output_style": "通用专题早报",
        "delivery_channel": "in_app",
        "intent_summary": "每天汇总 AI Agent 新闻",
        "report_sections": ["摘要", "核心事件"],
        "raw_task_description": "每天早上9点给我推送 AI Agent 新闻",
    }


@pytest.mark.asyncio
async def test_confirmation_is_bound_one_time_and_replay_returns_same_result() -> None:
    clock = _Clock()
    service = ScheduleConfirmationService(
        ttl_seconds=900,
        utcnow=clock.utcnow,
        monotonic=clock.monotonic_now,
    )
    issued = await service.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload={"task_type": "scheduled_push", "schedule": "0 9 * * *"},
        source_turn_id="turn-1",
    )
    calls = 0

    async def create_task(payload):
        nonlocal calls
        calls += 1
        return {"task": {"id": "task-1"}, "payload": payload}

    first = await service.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create_task,
    )
    replay = await service.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create_task,
    )

    assert calls == 1
    assert first["confirmation_status"] == "confirmed"
    assert first["replayed"] is False
    assert replay["task"]["id"] == "task-1"
    assert replay["replayed"] is True
    assert issued.expires_at == clock.now + timedelta(minutes=15)
    assert issued.token not in service.debug_token_digests()


@pytest.mark.asyncio
async def test_confirmation_rejects_wrong_binding_and_tampered_preview() -> None:
    service = ScheduleConfirmationService()
    issued = await service.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload={"task_type": "scheduled_push", "schedule": "0 9 * * *"},
    )

    with pytest.raises(ConfirmationError) as wrong_user:
        await service.confirm(
            user_id="user-2",
            conversation_id="conv-1",
            confirmation_id=issued.confirmation_id,
            token=issued.token,
            create=lambda payload: payload,
        )
    assert wrong_user.value.status_code == 403

    with pytest.raises(ConfirmationError) as tampered:
        await service.confirm(
            user_id="user-1",
            conversation_id="conv-1",
            confirmation_id=issued.confirmation_id,
            token=issued.token,
            create=lambda payload: payload,
            preview={**_preview(), "schedule": "* * * * *"},
        )
    assert tampered.value.status_code == 409


@pytest.mark.asyncio
async def test_cancel_and_expiry_never_create_task() -> None:
    clock = _Clock()
    service = ScheduleConfirmationService(
        ttl_seconds=900,
        utcnow=clock.utcnow,
        monotonic=clock.monotonic_now,
    )
    cancelled = await service.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload={"task_type": "scheduled_push", "schedule": "0 9 * * *"},
    )
    result = await service.cancel(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=cancelled.confirmation_id,
        token=cancelled.token,
    )
    assert result == {"confirmation_status": "cancelled", "replayed": False}

    with pytest.raises(ConfirmationError) as cancelled_confirm:
        await service.confirm(
            user_id="user-1",
            conversation_id="conv-1",
            confirmation_id=cancelled.confirmation_id,
            token=cancelled.token,
            create=lambda payload: payload,
        )
    assert cancelled_confirm.value.status_code == 409

    expired = await service.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload={"task_type": "scheduled_push", "schedule": "0 9 * * *"},
    )
    clock.advance(901)
    with pytest.raises(ConfirmationError) as expiry:
        await service.confirm(
            user_id="user-1",
            conversation_id="conv-1",
            confirmation_id=expired.confirmation_id,
            token=expired.token,
            create=lambda payload: payload,
        )
    assert expiry.value.status_code == 410


@pytest.mark.asyncio
async def test_concurrent_confirm_returns_conflict_while_first_is_consuming() -> None:
    service = ScheduleConfirmationService()
    issued = await service.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload={"task_type": "scheduled_push", "schedule": "0 9 * * *"},
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_create(payload):
        entered.set()
        await release.wait()
        return {"task": {"id": "task-1"}}

    first = asyncio.create_task(
        service.confirm(
            user_id="user-1",
            conversation_id="conv-1",
            confirmation_id=issued.confirmation_id,
            token=issued.token,
            create=slow_create,
        )
    )
    await entered.wait()
    with pytest.raises(ConfirmationError) as concurrent:
        await service.confirm(
            user_id="user-1",
            conversation_id="conv-1",
            confirmation_id=issued.confirmation_id,
            token=issued.token,
            create=slow_create,
        )
    assert concurrent.value.status_code == 409
    release.set()
    assert (await first)["task"]["id"] == "task-1"


@pytest.mark.asyncio
async def test_confirm_replay_does_not_duplicate_task_when_history_write_fails() -> None:
    class _Store:
        def __init__(self):
            self.created = []

        def create_task(self, payload):
            task = {"id": f"task-{len(self.created) + 1}", **payload}
            self.created.append(task)
            return task

        def get_or_create_conversation(self, user_id, kind, title):
            return {"id": "scheduled-conv", "title": title}

        def save_turn(self, *args, **kwargs):
            raise RuntimeError("history unavailable")

        def log(self, *args, **kwargs):
            raise RuntimeError("diagnostic log unavailable")

    class _Reports:
        search_service = None

    store = _Store()
    tasks = ScheduledTaskService(store, _Reports())
    confirmations = ScheduleConfirmationService()
    payload = {
        "user_id": "user-1",
        "task_type": "scheduled_push",
        "schedule": "0 9 * * *",
        "topics": ["AI Agent"],
        "category_scope": [],
        "source_scope": [],
        "output_style": "通用专题早报",
        "delivery_channel": "in_app",
        "raw_task_description": "每天早上9点推送 AI Agent 新闻",
        "parsed_workflow": {
            "intent_summary": "每日推送",
            "search_queries": [],
            "category_scope": [],
            "source_scope": [],
            "fetch_strategy": {},
            "report_style": {},
            "delivery": {},
        },
    }
    issued = await confirmations.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload=payload,
    )

    async def create(task_payload):
        return await tasks.create_from_schedule_preview(
            task_payload,
            user_id="user-1",
            original_message=payload["raw_task_description"],
        )

    first = await confirmations.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create,
    )
    replay = await confirmations.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create,
    )

    assert len(store.created) == 1
    assert first["task"]["id"] == replay["task"]["id"]
    assert first["completion_warnings"] == ["history_write_failed"]


@pytest.mark.asyncio
async def test_confirm_replay_does_not_duplicate_task_when_post_commit_trace_fails() -> None:
    class _Store:
        def __init__(self):
            self.created = []

        def create_task(self, payload):
            task = {"id": f"task-{len(self.created) + 1}", **payload}
            self.created.append(task)
            return task

        def get_or_create_conversation(self, user_id, kind, title):
            return {"id": "scheduled-conv", "title": title}

        def save_turn(self, *args, **kwargs):
            return "turn-1"

    class _Reports:
        search_service = None

    store = _Store()
    tasks = ScheduledTaskService(store, _Reports())
    confirmations = ScheduleConfirmationService()
    payload = {
        "user_id": "user-1",
        "task_type": "scheduled_push",
        "schedule": "0 9 * * *",
        "topics": ["AI Agent"],
        "category_scope": [],
        "source_scope": [],
        "output_style": "通用专题早报",
        "delivery_channel": "in_app",
        "raw_task_description": "每天早上9点推送 AI Agent 新闻",
        "parsed_workflow": {
            "intent_summary": "每日推送",
            "search_queries": [],
            "category_scope": [],
            "source_scope": [],
            "fetch_strategy": {},
            "report_style": {},
            "delivery": {},
        },
    }
    issued = await confirmations.issue(
        user_id="user-1",
        conversation_id="conv-1",
        preview=_preview(),
        task_payload=payload,
    )

    async def failing_trace(item):
        if item["stage"] == "任务落库":
            raise RuntimeError("trace transport unavailable")

    async def create(task_payload):
        return await tasks.create_from_schedule_preview(
            task_payload,
            user_id="user-1",
            original_message=payload["raw_task_description"],
            on_trace=failing_trace,
        )

    first = await confirmations.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create,
    )
    replay = await confirmations.confirm(
        user_id="user-1",
        conversation_id="conv-1",
        confirmation_id=issued.confirmation_id,
        token=issued.token,
        create=create,
    )

    assert len(store.created) == 1
    assert first["task"]["id"] == replay["task"]["id"]
