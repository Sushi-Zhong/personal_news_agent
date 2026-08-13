from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import time

import pytest

from personal_news_agent.core.models import NormalizedArticle
from personal_news_agent.core.text import content_hash
from personal_news_agent.services.store import NewsStore


def _article(article_id: str, title: str, *, category: str = "sports") -> NormalizedArticle:
    body = f"{title}。这是用于总事件聚合测试的正文。"
    now = datetime.now(timezone.utc)
    return NormalizedArticle(
        id=article_id,
        source_id=f"source_{article_id}",
        section_key=category,
        url=f"https://example.com/{article_id}",
        title=title,
        summary="",
        content=body,
        category=category,
        published_at=now,
        fetched_at=now,
        source_priority=1,
        keywords=[],
        entities=[],
        content_hash=content_hash(body),
    )


def _classification(**overrides):
    value = {
        "existing_topic_id": None,
        "canonical_name": "武大靖出任中国短道速滑队主教练",
        "subject": "武大靖",
        "action": "出任",
        "object": "中国短道速滑队主教练",
        "temporal_scope": None,
        "event_stage": "announced",
        "stage_label": "正式官宣",
        "article_type": "official_statement",
        "event_summary": "武大靖正式出任中国短道速滑队主教练。",
        "keywords": ["武大靖", "短道速滑", "主教练"],
        "classification_reason": "主体、动作和任职对象一致。",
        "confidence": 0.96,
    }
    value.update(overrides)
    return value


def _store(tmp_path) -> NewsStore:
    store = NewsStore(tmp_path / "events.db")
    store.init()
    return store


def test_new_topic_defaults_pending_and_confirmed_requires_active_fingerprint(tmp_path):
    store = _store(tmp_path)
    with store.connect() as conn:
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(news_topics)")}
        assert columns["identity_status"]["dflt_value"].strip("'") == "pending"
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """INSERT INTO news_topics(
                    id, category, name, first_seen_at, last_seen_at, identity_status, status
                ) VALUES ('invalid', 'sports', '无指纹事件', ?, ?, 'confirmed', 'active')""",
                (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
            )


def test_confirmed_topic_requires_matching_active_fingerprint_record(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        conn.execute(
            """INSERT INTO news_topics(
                 id, category, name, first_seen_at, last_seen_at, identity_status, status
               ) VALUES ('pending-topic', 'sports', '待确认事件', ?, ?, 'pending', 'active')""",
            (now, now),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE news_topics SET identity_status = 'confirmed', active_fingerprint_id = 'missing' WHERE id = 'pending-topic'"
            )


def test_existing_topic_keeps_its_active_fingerprint(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    first = store.apply_event_classification("a1", _classification(), candidate_topic_ids=set(), confidence_threshold=0.72)
    original = store.get_active_event_fingerprint(first["topic_id"])

    store.save_article(_article("a2", "官宣武大靖担任国家队主帅"))
    second = store.apply_event_classification(
        "a2",
        _classification(
            existing_topic_id=first["topic_id"],
            subject="模型本轮错误主体",
            action="模型本轮错误动作",
            object="模型本轮错误对象",
        ),
        candidate_topic_ids={first["topic_id"]},
        confidence_threshold=0.72,
    )

    assert second["topic_id"] == first["topic_id"]
    assert store.get_active_event_fingerprint(first["topic_id"])["event_key"] == original["event_key"]


def test_merged_topic_fingerprint_remains_resolvable_alias(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    primary = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖就任短道速滑主帅"))
    duplicate = store.apply_event_classification(
        "a2",
        _classification(canonical_name="武大靖就任短道速滑主帅", action="就任"),
        set(),
        confidence_threshold=0.72,
    )
    alias = store.get_active_event_fingerprint(duplicate["topic_id"])["event_key"]

    store.merge_news_topics(primary["topic_id"], duplicate["topic_id"], reason="测试合并")

    assert store.resolve_event_key(alias)["topic_id"] == primary["topic_id"]
    with store.connect() as conn:
        old = conn.execute("SELECT status, merged_into_topic_id FROM news_topics WHERE id = ?", (duplicate["topic_id"],)).fetchone()
        revision = conn.execute(
            "SELECT reason_code, source_article_ids_json FROM news_topic_summary_revisions WHERE topic_id = ? ORDER BY revision DESC LIMIT 1",
            (primary["topic_id"],),
        ).fetchone()
    assert dict(old) == {"status": "merged", "merged_into_topic_id": primary["topic_id"]}
    assert revision["reason_code"] == "topics_merged"
    assert set(json.loads(revision["source_article_ids_json"])) == {"a1", "a2"}


def test_classification_by_merged_fingerprint_reuses_final_topic(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    primary = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖就任短道速滑主帅"))
    duplicate = store.apply_event_classification(
        "a2", _classification(action="就任"), set(), confidence_threshold=0.72
    )
    store.merge_news_topics(primary["topic_id"], duplicate["topic_id"], reason="测试别名复用")
    store.save_article(_article("a3", "武大靖就任短道速滑主帅后续"))

    result = store.apply_event_classification(
        "a3", _classification(action="就任"), set(), confidence_threshold=0.72
    )

    assert result["topic_id"] == primary["topic_id"]


def test_confirmed_topic_blocks_active_fingerprint_supersede_or_delete(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    event = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    fingerprint = store.get_active_event_fingerprint(event["topic_id"])

    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE news_event_fingerprints SET status = 'superseded' WHERE id = ?", (fingerprint["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM news_event_fingerprints WHERE id = ?", (fingerprint["id"],))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("id", "efp_tampered"),
        ("algorithm_version", "event-fingerprint/v2"),
        ("normalized_payload_json", '{"subject_key":"tampered"}'),
        ("fingerprint_hash", "tampered"),
        ("event_key", "confirmed:tampered"),
    ],
)
def test_confirmed_topic_blocks_active_fingerprint_identity_mutation(tmp_path, column, value):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    event = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    fingerprint = store.get_active_event_fingerprint(event["topic_id"])

    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError, match="active confirmed fingerprint is referenced"):
            conn.execute(
                f"UPDATE news_event_fingerprints SET {column} = ? WHERE id = ?",
                (value, fingerprint["id"]),
            )


def test_event_timeline_projection_is_idempotent_per_topic_and_article(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    event = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)

    store.refresh_event_projection(event["topic_id"])
    store.refresh_event_projection(event["topic_id"])

    with store.connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM event_timelines WHERE topic_id = ? AND article_id = 'a1'",
            (event["topic_id"],),
        ).fetchone()[0]
        indexes = conn.execute("PRAGMA index_list(event_timelines)").fetchall()
    assert count == 1
    assert any(row["unique"] for row in indexes)


def test_projection_refresh_removes_stale_timeline_rows(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    event = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.refresh_event_projection(event["topic_id"])
    with store.connect() as conn:
        conn.execute("DELETE FROM news_topic_articles WHERE article_id = 'a1'")

    store.refresh_event_projection(event["topic_id"])

    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_timelines WHERE topic_id = ?", (event["topic_id"],)).fetchone()[0] == 0


def test_low_confidence_uses_pending_key_and_writes_article_audit(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "尚未确认的报道"))

    result = store.apply_event_classification(
        "a1",
        _classification(confidence=0.73),
        set(),
        confidence_threshold=0.80,
    )

    assert result["identity_status"] == "pending"
    assert result["event_key"].startswith("pending:article/v1:")
    with store.connect() as conn:
        audit = conn.execute(
            "SELECT decision, article_id, to_topic_id FROM news_event_classification_audits WHERE article_id = 'a1'"
        ).fetchone()
    assert dict(audit) == {"decision": "pending", "article_id": "a1", "to_topic_id": result["topic_id"]}


def test_low_confidence_does_not_merge_ai_selected_existing_topic(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    confirmed = store.apply_event_classification(
        "a1", _classification(), set(), confidence_threshold=0.72
    )
    store.save_article(_article("a2", "未经确认的近似消息"))

    pending = store.apply_event_classification(
        "a2",
        _classification(existing_topic_id=confirmed["topic_id"], confidence=0.4),
        {confirmed["topic_id"]},
        confidence_threshold=0.72,
    )

    assert pending["identity_status"] == "pending"
    assert pending["topic_id"] != confirmed["topic_id"]
    assert pending["event_key"].startswith("pending:article/v1:")


def test_analysis_article_does_not_replace_confirmed_summary(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    first = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖能否带队走出低谷"))

    store.apply_event_classification(
        "a2",
        _classification(
            existing_topic_id=first["topic_id"],
            article_type="analysis",
            event_summary="评论认为武大靖执教前景仍存在不确定性。",
        ),
        {first["topic_id"]},
        confidence_threshold=0.72,
    )

    with store.connect() as conn:
        topic = conn.execute("SELECT summary, summary_revision FROM news_topics WHERE id = ?", (first["topic_id"],)).fetchone()
    assert topic["summary"] == "武大靖正式出任中国短道速滑队主教练。"
    assert topic["summary_revision"] == 1


def test_same_stage_paraphrase_does_not_create_summary_revision(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    first = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "官宣武大靖担任短道速滑队主帅"))

    store.apply_event_classification(
        "a2",
        _classification(
            existing_topic_id=first["topic_id"],
            article_type="fact_report",
            event_summary="中国短道速滑队已经正式任命武大靖担任主教练。",
        ),
        {first["topic_id"]},
        confidence_threshold=0.72,
    )

    with store.connect() as conn:
        topic = conn.execute(
            "SELECT summary, summary_revision FROM news_topics WHERE id = ?", (first["topic_id"],)
        ).fetchone()
    assert topic["summary"] == "武大靖正式出任中国短道速滑队主教练。"
    assert topic["summary_revision"] == 1


def test_same_stage_explicit_new_fact_creates_fact_added_revision(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    first = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖公布教练团队"))

    store.apply_event_classification(
        "a2",
        _classification(
            existing_topic_id=first["topic_id"],
            event_summary="武大靖公布了新的教练团队名单。",
            summary_decision="revise",
            summary_reason_code="fact_added",
        ),
        {first["topic_id"]},
        confidence_threshold=0.72,
    )

    with store.connect() as conn:
        revision = conn.execute(
            "SELECT reason_code, source_article_ids_json FROM news_topic_summary_revisions WHERE topic_id = ? ORDER BY revision DESC LIMIT 1",
            (first["topic_id"],),
        ).fetchone()
    assert revision["reason_code"] == "fact_added"
    assert json.loads(revision["source_article_ids_json"]) == ["a2"]


def test_same_stage_explicit_correction_creates_correction_revision(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    first = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "官方更正武大靖任职范围"))

    store.apply_event_classification(
        "a2",
        _classification(
            existing_topic_id=first["topic_id"],
            event_summary="此前信息已被更正：武大靖负责短道速滑国家队训练工作。",
            summary_decision="revise",
            summary_reason_code="correction",
        ),
        {first["topic_id"]},
        confidence_threshold=0.72,
    )

    with store.connect() as conn:
        revision = conn.execute(
            "SELECT summary, reason_code FROM news_topic_summary_revisions WHERE topic_id = ? ORDER BY revision DESC LIMIT 1",
            (first["topic_id"],),
        ).fetchone()
    assert revision["reason_code"] == "correction"
    assert "此前信息已被更正" in revision["summary"]


def test_cross_category_subject_candidate_is_not_starved_by_same_category_limit(tmp_path):
    store = _store(tmp_path)
    now = datetime.now(timezone.utc).isoformat()
    with store.connect() as conn:
        for index in range(25):
            topic_id = f"politics-{index:02d}"
            conn.execute(
                """INSERT INTO news_topics(
                     id, category, name, first_seen_at, last_seen_at, status, identity_status
                   ) VALUES (?, 'politics', ?, ?, ?, 'active', 'pending')""",
                (topic_id, f"无关政治事件{index}", now, now),
            )
        conn.execute(
            """INSERT INTO news_topics(
                 id, category, name, first_seen_at, last_seen_at, status, identity_status
               ) VALUES ('sports-event', 'sports', '武大靖出任中国短道速滑队主教练', ?, ?, 'active', 'pending')""",
            (now, now),
        )
        conn.execute(
            """INSERT INTO news_articles(id, source_id, url, url_hash, title, content, category, status)
               VALUES ('existing', 'source', 'https://example.com/existing', 'existing', '武大靖出任主教练', '', 'sports', 'active')"""
        )
        conn.execute(
            """INSERT INTO news_topic_articles(article_id, topic_id, subject, event_summary, confidence, created_at)
               VALUES ('existing', 'sports-event', '武大靖', '武大靖出任主教练', 0.9, ?)""",
            (now,),
        )

    candidates = store.list_event_candidates(
        {"category": "politics", "title": "武大靖已出任中国短道速滑队主教练", "content": ""},
        limit=20,
    )

    assert len(candidates) <= 20
    assert "sports-event" in {item["id"] for item in candidates}


def test_explicit_stage_advance_creates_traceable_summary_revision(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖拟出任主教练"))
    first = store.apply_event_classification(
        "a1",
        _classification(
            event_stage="proposed",
            article_type="fact_report",
            event_summary="武大靖拟出任中国短道速滑队主教练。",
        ),
        set(),
        confidence_threshold=0.72,
    )
    store.save_article(_article("a2", "官宣武大靖出任主教练"))

    store.apply_event_classification(
        "a2",
        _classification(existing_topic_id=first["topic_id"]),
        {first["topic_id"]},
        confidence_threshold=0.72,
    )

    with store.connect() as conn:
        revisions = conn.execute(
            """SELECT revision, reason_code, source_article_ids_json, stage_snapshot_json
               FROM news_topic_summary_revisions WHERE topic_id = ? ORDER BY revision""",
            (first["topic_id"],),
        ).fetchall()
    assert [row["reason_code"] for row in revisions] == ["created", "stage_advanced"]
    assert revisions[1]["source_article_ids_json"] == '["a2"]'
    assert revisions[1]["stage_snapshot_json"] == '["proposed", "announced"]'


def test_legacy_timeline_duplicates_are_deduplicated_before_unique_index(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            """CREATE TABLE event_timelines(
                 id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, event_date TEXT,
                 event_title TEXT NOT NULL, event_summary TEXT, actors_json TEXT,
                 source_article_ids_json TEXT, confidence REAL DEFAULT 0.0,
                 topic_id TEXT, article_id TEXT
               )"""
        )
        conn.execute(
            "INSERT INTO event_timelines(id, cluster_id, event_title, topic_id, article_id) VALUES ('old-1', 't1', '旧', 't1', 'a1')"
        )
        conn.execute(
            "INSERT INTO event_timelines(id, cluster_id, event_title, topic_id, article_id) VALUES ('old-2', 't1', '新', 't1', 'a1')"
        )

    store = NewsStore(database)
    store.init()

    with store.connect() as conn:
        rows = conn.execute(
            "SELECT id FROM event_timelines WHERE topic_id = 't1' AND article_id = 'a1'"
        ).fetchall()
    assert len(rows) == 1


def test_two_workers_reuse_one_confirmed_fingerprint(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    store.save_article(_article("a2", "官宣武大靖担任国家队主帅"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda article_id: store.apply_event_classification(
                    article_id, _classification(), set(), confidence_threshold=0.72
                ),
                ["a1", "a2"],
            )
        )

    assert results[0]["topic_id"] == results[1]["topic_id"]
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_topics WHERE identity_status = 'confirmed'").fetchone()[0] == 1
        assert conn.execute("SELECT article_count FROM news_topics WHERE id = ?", (results[0]["topic_id"],)).fetchone()[0] == 2


def test_lock_retry_exhaustion_creates_no_pending_and_writes_failed_audit(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    attempts = 0

    def always_locked(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "_apply_event_classification_once", always_locked)
    monkeypatch.setattr("personal_news_agent.services.store.time.sleep", lambda _delay: None)

    with pytest.raises(sqlite3.OperationalError, match="database is locked"):
        store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)

    assert attempts == 4
    with store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM news_topics").fetchone()[0] == 0
        audit = conn.execute(
            "SELECT decision, reason_code FROM news_event_classification_audits WHERE article_id = 'a1'"
        ).fetchone()
    assert dict(audit) == {"decision": "failed", "reason_code": "sqlite_lock_exhausted"}


def test_real_sqlite_write_lock_uses_bounded_retries_then_persists_failed_audit(tmp_path, monkeypatch):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))

    @contextmanager
    def fast_connect():
        conn = sqlite3.connect(store.db_path, timeout=0.005)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(store, "connect", fast_connect)
    blocker = sqlite3.connect(store.db_path, timeout=0)
    blocker.execute("BEGIN IMMEDIATE")

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                store.apply_event_classification,
                "a1",
                _classification(),
                set(),
                confidence_threshold=0.72,
            )
            # Hold beyond both bounded retry windows; the audit must be
            # deferred and flushed by the next successful database access.
            time.sleep(1.3)
            blocker.rollback()
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                future.result(timeout=5)
    finally:
        blocker.close()

    with store.connect() as conn:
        assert store.flush_deferred_classification_audits(conn) == 1
        assert conn.execute("SELECT COUNT(*) FROM news_topics").fetchone()[0] == 0
        audit = conn.execute(
            "SELECT decision, reason_code FROM news_event_classification_audits WHERE article_id = 'a1'"
        ).fetchone()
    assert dict(audit) == {"decision": "failed", "reason_code": "sqlite_lock_exhausted"}


def test_pending_article_can_be_reclassified_to_confirmed_and_supersedes_pending_key(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "尚未确认的报道"))
    pending = store.apply_event_classification(
        "a1", _classification(confidence=0.4), set(), confidence_threshold=0.72
    )

    confirmed = store.apply_event_classification(
        "a1", _classification(confidence=0.96), set(), confidence_threshold=0.72
    )

    assert confirmed["identity_status"] == "confirmed"
    assert confirmed["topic_id"] != pending["topic_id"]
    assert store.resolve_event_key(pending["event_key"])["topic_id"] == confirmed["topic_id"]
    with store.connect() as conn:
        old_topic = conn.execute(
            "SELECT status, merged_into_topic_id FROM news_topics WHERE id = ?", (pending["topic_id"],)
        ).fetchone()
        old_fingerprint = conn.execute(
            "SELECT status, superseded_at FROM news_event_fingerprints WHERE event_key = ?", (pending["event_key"],)
        ).fetchone()
        audit = conn.execute(
            "SELECT decision, from_topic_id, to_topic_id FROM news_event_classification_audits WHERE article_id = 'a1' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    assert dict(old_topic) == {"status": "merged", "merged_into_topic_id": confirmed["topic_id"]}
    assert old_fingerprint["status"] == "superseded"
    assert old_fingerprint["superseded_at"]
    assert dict(audit) == {"decision": "reclassified", "from_topic_id": pending["topic_id"], "to_topic_id": confirmed["topic_id"]}


def test_pending_reclassification_resolves_merged_confirmed_fingerprint_alias(tmp_path):
    store = _store(tmp_path)
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    primary = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖就任短道速滑主帅"))
    duplicate = store.apply_event_classification(
        "a2", _classification(action="就任"), set(), confidence_threshold=0.72
    )
    store.merge_news_topics(primary["topic_id"], duplicate["topic_id"], reason="测试别名")
    store.save_article(_article("a3", "尚待确认的就任消息"))
    store.apply_event_classification(
        "a3", _classification(action="就任", confidence=0.2), set(), confidence_threshold=0.72
    )

    result = store.apply_event_classification(
        "a3", _classification(action="就任", confidence=0.96), set(), confidence_threshold=0.72
    )

    assert result["topic_id"] == primary["topic_id"]
