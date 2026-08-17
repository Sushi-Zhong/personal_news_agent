from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable
from urllib.parse import urlparse

from personal_news_agent.core.models import EvidenceRef, SearchResult
from personal_news_agent.services.article_fetch import canonicalize_url


class EvidenceLedger:
    """Per-request ledger that only accepts evidence observed by application code."""

    def __init__(self) -> None:
        self._references: list[EvidenceRef] = []
        self._by_key: dict[str, EvidenceRef] = {}

    @property
    def references(self) -> tuple[EvidenceRef, ...]:
        return tuple(self._references)

    def add(
        self,
        item: SearchResult | dict[str, Any],
        *,
        origin: str | None = None,
        claim_role: str = "context",
        dedupe_key: str | None = None,
    ) -> EvidenceRef:
        payload = item.model_dump(mode="python") if hasattr(item, "model_dump") else dict(item)
        url = canonicalize_url(str(payload.get("url") or ""))
        parsed_url = urlparse(url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
            raise ValueError("Evidence HTTP(S) URL is required")
        key = dedupe_key or url
        existing = self._by_key.get(key)
        if existing:
            return existing
        reference = EvidenceRef(
            index=len(self._references) + 1,
            title=str(payload.get("title") or url),
            url=url,
            source_id=payload.get("source_id"),
            published_at=_published_at(payload.get("published_at")),
            origin=origin or str(payload.get("origin") or "local"),
            claim_role=claim_role,
        )
        self._references.append(reference)
        self._by_key[key] = reference
        return reference

    def validate_model_references(self, items: Iterable[dict[str, Any] | int]) -> list[EvidenceRef]:
        valid: list[EvidenceRef] = []
        seen: set[int] = set()
        for item in items:
            payload = {"index": item} if isinstance(item, int) else item
            try:
                index = int(payload.get("index") or 0)
            except (TypeError, ValueError):
                continue
            if index < 1 or index > len(self._references) or index in seen:
                continue
            reference = self._references[index - 1]
            declared_url = str(payload.get("url") or "").strip()
            if declared_url and canonicalize_url(declared_url) != reference.url:
                continue
            seen.add(index)
            valid.append(reference)
        return valid


def _published_at(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
