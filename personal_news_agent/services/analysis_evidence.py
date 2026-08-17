from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re
from typing import Any, Iterable

from personal_news_agent.core.models import TimeRange


def filter_recent_evidence(
    items: Iterable[Any],
    time_range: TimeRange,
    *,
    now: datetime | None = None,
) -> list[Any]:
    current = _aware_datetime(now) or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=time_range.days)
    filtered: list[Any] = []
    for item in items:
        published_at = _aware_datetime(_value(item, "published_at"))
        if published_at is not None:
            if published_at >= cutoff:
                filtered.append(item)
            continue
        if str(_value(item, "origin") or "") == "external":
            filtered.append(item)
    return filtered


def filter_topic_evidence(items: Iterable[Any], topic: str) -> list[Any]:
    """Require explicit Latin entity names from the topic to remain present."""
    entities = [
        token.casefold()
        for token in re.findall(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9+#._-]{1,}(?![A-Za-z0-9])", topic or "")
        if len(token) >= 3 or token.isupper()
    ]
    candidates = list(items)
    if not entities:
        return candidates

    filtered: list[Any] = []
    for item in candidates:
        text = " ".join(
            str(_value(item, field) or "")
            for field in ("title", "summary", "content_excerpt", "content", "keywords")
        ).casefold()
        if all(entity in text for entity in entities):
            filtered.append(item)
    return filtered


def _value(item: Any, name: str) -> Any:
    if isinstance(item, dict):
        return item.get(name)
    return getattr(item, name, None)


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
