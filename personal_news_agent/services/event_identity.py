from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import unicodedata
from typing import Any


CONFIRMED_FINGERPRINT_VERSION = "event-fingerprint/v1"
PENDING_ARTICLE_VERSION = "pending-article/v1"
PENDING_LEGACY_TOPIC_VERSION = "pending-legacy-topic/v1"
EVENT_STAGES = frozenset({"initial", "proposed", "announced", "opening", "midday", "closing", "follow_up", "resolved", "unknown"})
ARTICLE_TYPES = frozenset({"fact_report", "live_update", "official_statement", "analysis", "opinion", "explainer", "recap", "rumor", "unknown"})
FACTUAL_ARTICLE_TYPES = frozenset({"fact_report", "live_update", "official_statement"})


@dataclass(frozen=True)
class EventFingerprint:
    algorithm_version: str
    normalized_payload: dict[str, Any]
    fingerprint_hash: str
    event_key: str
    key_kind: str


def _normalize_text(value: str | None) -> str:
    return unicodedata.normalize("NFC", " ".join(str(value or "").split())).strip()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def confirmed_event_key(*, subject: str, action: str, object_: str, temporal_scope: str | None) -> EventFingerprint:
    payload = {
        "subject_key": _normalize_text(subject),
        "action_key": _normalize_text(action),
        "object_key": _normalize_text(object_),
        "temporal_scope": _normalize_text(temporal_scope) or None,
    }
    digest = hashlib.sha256(f"{CONFIRMED_FINGERPRINT_VERSION}\n{_canonical_json(payload)}".encode("utf-8")).hexdigest()
    return EventFingerprint(
        algorithm_version=CONFIRMED_FINGERPRINT_VERSION,
        normalized_payload=payload,
        fingerprint_hash=digest,
        event_key=f"confirmed:{CONFIRMED_FINGERPRINT_VERSION}:{digest}",
        key_kind="confirmed",
    )


def pending_article_event_key(article_id: str) -> EventFingerprint:
    payload = {"article_id": str(article_id)}
    digest = hashlib.sha256(str(article_id).encode("utf-8")).hexdigest()
    return EventFingerprint(
        algorithm_version=PENDING_ARTICLE_VERSION,
        normalized_payload=payload,
        fingerprint_hash=digest,
        event_key=f"pending:article/v1:{digest}",
        key_kind="pending",
    )


def pending_legacy_topic_event_key(topic_id: str) -> EventFingerprint:
    payload = {"legacy_topic_id": str(topic_id)}
    digest = hashlib.sha256(str(topic_id).encode("utf-8")).hexdigest()
    return EventFingerprint(
        algorithm_version=PENDING_LEGACY_TOPIC_VERSION,
        normalized_payload=payload,
        fingerprint_hash=digest,
        event_key=f"pending:legacy-topic/v1:{digest}",
        key_kind="pending",
    )
