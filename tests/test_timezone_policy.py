from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from personal_news_agent.config import Settings
from personal_news_agent.services.tasks import ScheduledTaskService, next_run_at


class _Store:
    def __init__(self) -> None:
        self.created: list[dict] = []

    def create_task(self, payload):
        self.created.append(payload)
        return {"id": "task-1", **payload}

    def log(self, *args, **kwargs):
        return None


class _Reports:
    search_service = None


class _DisabledLLM:
    configured = False


def test_default_application_timezone_is_shanghai_when_server_timezone_is_utc() -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        background_crawl_enabled=False,
        app_timezone="Asia/Shanghai",
    )
    base = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)

    result = next_run_at("0 9 * * *", base=base, app_timezone=ZoneInfo(settings.app_timezone))

    assert result == "2026-08-14T01:00:00+00:00"


def test_invalid_application_timezone_is_rejected() -> None:
    with pytest.raises(ValueError, match="PNA_APP_TIMEZONE"):
        Settings(database_url="sqlite:///:memory:", app_timezone="Mars/Olympus")


def test_schedule_preview_uses_application_timezone_without_creating_task() -> None:
    store = _Store()
    tasks = ScheduledTaskService(
        store,
        _Reports(),
        llm_client=_DisabledLLM(),
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )

    preview = asyncio.run(
        tasks.prepare_schedule_preview(
            "user-1",
            "每天早上9点给我推送 AI Agent 新闻",
            base=datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc),
        )
    )

    assert store.created == []
    assert preview["schedule"] == "0 9 * * *"
    assert preview["timezone"] == "Asia/Shanghai"
    assert preview["next_run_at"] == "2026-08-14T01:00:00+00:00"
    assert preview["topics"] == ["AI Agent"]


def test_task_level_timezone_is_not_supported() -> None:
    tasks = ScheduledTaskService(
        _Store(),
        _Reports(),
        llm_client=_DisabledLLM(),
        app_timezone=ZoneInfo("Asia/Shanghai"),
    )

    with pytest.raises(ValueError, match="task-level timezone"):
        tasks.create_task(
            {
                "user_id": "user-1",
                "task_type": "scheduled_push",
                "schedule": "0 9 * * *",
                "timezone": "America/New_York",
            }
        )
