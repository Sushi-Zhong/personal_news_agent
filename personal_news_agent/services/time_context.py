from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


DEFAULT_APP_TIMEZONE = "Asia/Shanghai"


def application_timezone(name: str = DEFAULT_APP_TIMEZONE) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid PNA_APP_TIMEZONE: {name}") from exc


def format_in_application_timezone(value: datetime, app_timezone: ZoneInfo) -> str:
    if value.tzinfo is None:
        raise ValueError("Timezone-aware datetime is required")
    return value.astimezone(app_timezone).isoformat()
