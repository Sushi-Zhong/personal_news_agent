from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

from personal_news_agent.services.store import NewsStore
from personal_news_agent.services.event_identity import pending_legacy_topic_event_key
from scripts.repair_duplicate_news_topics import apply_repairs, find_repairs
from tests.test_event_aggregation import _article, _classification


ROOT = Path(__file__).resolve().parents[1]


def _duplicates(tmp_path):
    store = NewsStore(tmp_path / "repair.db"); store.init()
    store.save_article(_article("a1", "武大靖正式出任主教练"))
    primary = store.apply_event_classification("a1", _classification(), set(), confidence_threshold=0.72)
    store.save_article(_article("a2", "武大靖就任短道速滑主帅"))
    duplicate = store.apply_event_classification(
        "a2",
        _classification(canonical_name="武大靖就任短道速滑主帅", action="就任"),
        set(),
        confidence_threshold=0.72,
    )
    return store, primary, duplicate


def test_repair_dry_run_reports_without_changing_topics(tmp_path):
    store, primary, duplicate = _duplicates(tmp_path)
    repairs = find_repairs(store)

    assert repairs == [{
        "primary_topic_id": primary["topic_id"],
        "duplicate_topic_id": duplicate["topic_id"],
        "article_ids": ["a2"],
        "reason": "same confirmed subject and object with equivalent appointment action",
        "confidence": 0.99,
    }]
    with store.connect() as conn:
        assert conn.execute("SELECT status FROM news_topics WHERE id = ?", (duplicate["topic_id"],)).fetchone()[0] == "active"


def test_repair_apply_preserves_alias_audits_each_article_and_is_idempotent(tmp_path):
    store, primary, duplicate = _duplicates(tmp_path)
    alias = store.get_active_event_fingerprint(duplicate["topic_id"])["event_key"]

    first = apply_repairs(store, find_repairs(store))
    second = apply_repairs(store, find_repairs(store))

    assert first == {"merged": 1, "moved_articles": 1, "skipped": 0, "conflicts": 0, "failed": 0}
    assert second == {"merged": 0, "moved_articles": 0, "skipped": 0, "conflicts": 0, "failed": 0}
    assert store.resolve_event_key(alias)["topic_id"] == primary["topic_id"]
    with store.connect() as conn:
        audit = conn.execute(
            "SELECT decision, from_topic_id, to_topic_id FROM news_event_classification_audits WHERE article_id = 'a2' AND decision = 'reclassified'"
        ).fetchone()
        link = conn.execute("SELECT topic_id FROM news_topic_articles WHERE article_id = 'a2'").fetchone()
        revision = conn.execute(
            "SELECT reason_code, source_article_ids_json FROM news_topic_summary_revisions WHERE topic_id = ? ORDER BY revision DESC LIMIT 1",
            (primary["topic_id"],),
        ).fetchone()
    assert dict(audit) == {"decision": "reclassified", "from_topic_id": duplicate["topic_id"], "to_topic_id": primary["topic_id"]}
    assert link["topic_id"] == primary["topic_id"]
    assert revision["reason_code"] == "topics_merged"
    assert set(json.loads(revision["source_article_ids_json"])) == {"a1", "a2"}


def test_repair_finds_equivalent_legacy_appointment_topics_but_not_same_subject_other_event(tmp_path):
    store = NewsStore(tmp_path / "legacy-repair.db"); store.init()
    now = "2026-08-12T00:00:00+00:00"
    with store.connect() as conn:
        for topic_id, category, name, count in (
            ("primary", "sports", "武大靖出任中国短道速滑队主教练", 4),
            ("duplicate-sports", "sports", "武大靖执教中国短道速滑队", 3),
            ("duplicate-politics", "politics", "武大靖执教中国短道速滑队", 2),
            ("other", "sports", "武大靖谈退役后的个人生活", 1),
        ):
            conn.execute(
                """INSERT INTO news_topics(
                     id, category, name, first_seen_at, last_seen_at, article_count, status,
                     canonical_name, primary_category, identity_status
                   ) VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, 'pending')""",
                (topic_id, category, name, now, now, count, name, category),
            )
        for index, topic_id in enumerate(("primary", "duplicate-sports", "duplicate-politics", "other"), start=1):
            article_id = f"legacy-{index}"
            conn.execute(
                """INSERT INTO news_articles(id, source_id, url, url_hash, title, content, category, status)
                   VALUES (?, 'legacy', ?, ?, ?, '', 'sports', 'active')""",
                (article_id, f"https://example.com/{article_id}", article_id, topic_id),
            )
            conn.execute(
                """INSERT INTO news_topic_articles(article_id, topic_id, subject, event_summary, confidence, created_at)
                   VALUES (?, ?, '武大靖', ?, 0.8, ?)""",
                (article_id, topic_id, topic_id, now),
            )

    repairs = find_repairs(store)

    assert {(item["primary_topic_id"], item["duplicate_topic_id"]) for item in repairs} == {
        ("primary", "duplicate-sports"),
        ("primary", "duplicate-politics"),
    }
    assert all(item["duplicate_topic_id"] != "other" for item in repairs)

    result = apply_repairs(store, repairs)

    assert result == {"merged": 2, "moved_articles": 2, "skipped": 0, "conflicts": 0, "failed": 0}
    with store.connect() as conn:
        primary = conn.execute(
            "SELECT identity_status, active_fingerprint_id, article_count FROM news_topics WHERE id = 'primary'"
        ).fetchone()
        merged = conn.execute(
            "SELECT status, merged_into_topic_id FROM news_topics WHERE id = 'duplicate-politics'"
        ).fetchone()
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM news_event_classification_audits WHERE decision = 'reclassified'"
        ).fetchone()[0]
    assert primary["identity_status"] == "confirmed"
    assert primary["active_fingerprint_id"]
    assert primary["article_count"] == 3
    assert dict(merged) == {"status": "merged", "merged_into_topic_id": "primary"}
    assert audit_count == 2
    assert store.get_active_event_fingerprint("duplicate-sports")["event_key"].startswith("pending:legacy-topic/v1:")


def test_repair_dry_run_does_not_create_missing_database(tmp_path):
    database = tmp_path / "missing.db"

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "repair_duplicate_news_topics.py"),
            "--database",
            str(database),
            "--dry-run",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "does not exist" in result.stderr
    assert not database.exists()
