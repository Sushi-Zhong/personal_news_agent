from __future__ import annotations

from personal_news_agent.services.event_identity import (
    CONFIRMED_FINGERPRINT_VERSION,
    confirmed_event_key,
    pending_article_event_key,
)


def test_confirmed_event_key_is_stable_and_versioned():
    first = confirmed_event_key(
        subject="武大靖",
        action="出任",
        object_="中国短道速滑队主教练",
        temporal_scope=None,
    )
    second = confirmed_event_key(
        subject="武大靖",
        action="出任",
        object_="中国短道速滑队主教练",
        temporal_scope=None,
    )

    assert first == second
    assert first.algorithm_version == CONFIRMED_FINGERPRINT_VERSION
    assert first.event_key.startswith("confirmed:event-fingerprint/v1:")
    assert first.normalized_payload == {
        "subject_key": "武大靖",
        "action_key": "出任",
        "object_key": "中国短道速滑队主教练",
        "temporal_scope": None,
    }


def test_pending_event_key_is_isolated_per_article():
    first = pending_article_event_key("article-1")
    second = pending_article_event_key("article-2")

    assert first.event_key.startswith("pending:article/v1:")
    assert first.event_key != second.event_key
