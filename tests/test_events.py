from __future__ import annotations

import json

from personal_news_agent.services.events import EventDiscoveryService
from personal_news_agent.services.store import NewsStore

from tests.test_event_aggregation import _article, _classification


def _confirmed_event(store: NewsStore, article_id: str = "a1") -> dict:
    store.save_article(_article(article_id, "武大靖正式出任主教练"))
    return store.apply_event_classification(
        article_id,
        _classification(),
        set(),
        confidence_threshold=0.72,
    )


def test_event_discovery_rebuilds_projections_from_canonical_topics(tmp_path):
    store = NewsStore(tmp_path / "events.db"); store.init()
    event = _confirmed_event(store)
    service = EventDiscoveryService(store)

    rebuilt = service.rebuild()

    assert rebuilt[0].id == event["topic_id"]
    with store.connect() as conn:
        cluster = conn.execute("SELECT * FROM topic_clusters WHERE id = ?", (event["topic_id"],)).fetchone()
        timeline = conn.execute("SELECT topic_id, article_id FROM event_timelines").fetchone()
    assert json.loads(cluster["article_ids_json"]) == ["a1"]
    assert dict(timeline) == {"topic_id": event["topic_id"], "article_id": "a1"}


def test_event_list_is_read_only_and_falls_back_to_authority_when_projection_missing(tmp_path):
    store = NewsStore(tmp_path / "events.db"); store.init()
    event = _confirmed_event(store)
    service = EventDiscoveryService(store)
    with store.connect() as conn:
        conn.execute("DELETE FROM topic_clusters")
        conn.execute("DELETE FROM event_timelines")
        conn.execute("DELETE FROM operation_logs")

    items = service.list_events()

    assert items[0]["id"] == event["topic_id"]
    assert items[0]["event_key"].startswith("confirmed:event-fingerprint/v1:")
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_clusters").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM event_timelines").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM operation_logs").fetchone()[0] == 0


def test_projection_can_be_cleared_and_rebuilt_idempotently(tmp_path):
    store = NewsStore(tmp_path / "events.db"); store.init()
    event = _confirmed_event(store)
    service = EventDiscoveryService(store)
    service.rebuild()
    with store.connect() as conn:
        conn.execute("DELETE FROM topic_clusters")
        conn.execute("DELETE FROM event_timelines")

    service.rebuild()
    service.rebuild()

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_clusters WHERE id = ?", (event["topic_id"],)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event_timelines WHERE topic_id = ?", (event["topic_id"],)).fetchone()[0] == 1


def test_rebuild_prunes_projection_rows_for_merged_topics(tmp_path):
    store = NewsStore(tmp_path / "events.db"); store.init()
    primary = _confirmed_event(store, "a1")
    store.save_article(_article("a2", "武大靖就任短道速滑主帅"))
    duplicate = store.apply_event_classification(
        "a2", _classification(action="就任"), set(), confidence_threshold=0.72
    )
    service = EventDiscoveryService(store)
    service.rebuild()
    store.merge_news_topics(primary["topic_id"], duplicate["topic_id"], reason="测试投影清理")

    service.rebuild()

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM topic_clusters WHERE id = ?", (duplicate["topic_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM event_timelines WHERE topic_id = ?", (duplicate["topic_id"],)).fetchone()[0] == 0
