from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from personal_news_agent.services.store import NewsStore


ROOT = Path(__file__).resolve().parents[1]


def test_sqlite_event_migration_defaults_legacy_topics_pending_and_deduplicates_timeline(tmp_path):
    database = tmp_path / "legacy.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE news_topics(id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
              summary TEXT, keywords_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              article_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active');
            INSERT INTO news_topics(id, category, name, first_seen_at, last_seen_at)
              VALUES ('legacy', 'sports', '旧主题', '2026-01-01', '2026-01-01');
            CREATE TABLE news_topic_articles(article_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
              subject TEXT, event_summary TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE topic_clusters(id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
              keywords_json TEXT, entities_json TEXT, article_ids_json TEXT, source_count INTEGER,
              article_count INTEGER, hot_score REAL, first_seen_at TEXT, latest_seen_at TEXT, status TEXT DEFAULT 'active');
            CREATE TABLE event_timelines(id TEXT PRIMARY KEY, cluster_id TEXT NOT NULL, event_date TEXT,
              event_title TEXT NOT NULL, event_summary TEXT, actors_json TEXT,
              source_article_ids_json TEXT, confidence REAL DEFAULT 0.0);
            INSERT INTO event_timelines(id, cluster_id, event_title) VALUES ('one', 'legacy', '一');
            INSERT INTO event_timelines(id, cluster_id, event_title) VALUES ('two', 'legacy', '二');
            """
        )
        conn.executescript((ROOT / "sql" / "upgrade_pna_news_event_aggregation_sqlite.sql").read_text(encoding="utf-8"))
        topic = conn.execute("SELECT identity_status, active_fingerprint_id FROM news_topics WHERE id = 'legacy'").fetchone()
        indexes = conn.execute("PRAGMA index_list(event_timelines)").fetchall()

    assert topic == ("pending", None)
    assert any(row[1] == "idx_event_timelines_topic_article" and row[2] == 1 for row in indexes)


def test_store_init_backfills_versioned_pending_legacy_key_and_summary_revision(tmp_path):
    database = tmp_path / "legacy-store.db"
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE news_topics(id TEXT PRIMARY KEY, category TEXT NOT NULL, name TEXT NOT NULL,
              summary TEXT, keywords_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
              article_count INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active');
            INSERT INTO news_topics(id, category, name, summary, first_seen_at, last_seen_at)
              VALUES ('legacy', 'sports', '武大靖执教中国短道速滑队', '武大靖已开始执教。', '2026-01-01', '2026-01-02');
            CREATE TABLE news_topic_articles(article_id TEXT PRIMARY KEY, topic_id TEXT NOT NULL,
              subject TEXT, event_summary TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            """
        )

    store = NewsStore(database)
    store.init()

    fingerprint = store.get_active_event_fingerprint("legacy")
    with store.connect() as conn:
        topic = conn.execute(
            "SELECT identity_status, active_fingerprint_id, summary_revision FROM news_topics WHERE id = 'legacy'"
        ).fetchone()
        revision = conn.execute(
            "SELECT generation_source, reason_code FROM news_topic_summary_revisions WHERE topic_id = 'legacy'"
        ).fetchone()
    assert fingerprint["event_key"].startswith("pending:legacy-topic/v1:")
    assert topic["identity_status"] == "pending"
    assert topic["active_fingerprint_id"] == fingerprint["id"]
    assert topic["summary_revision"] == 1
    assert dict(revision) == {"generation_source": "migration", "reason_code": "created"}


def test_store_init_replaces_legacy_weak_confirmed_trigger(tmp_path):
    database = tmp_path / "weak-trigger.db"
    store = NewsStore(database)
    store.init()
    with store.connect() as conn:
        conn.execute("DROP TRIGGER trg_news_topics_confirmed_requires_fingerprint_update")
        conn.execute(
            """CREATE TRIGGER trg_news_topics_confirmed_requires_fingerprint_update
               BEFORE UPDATE OF identity_status, active_fingerprint_id ON news_topics
               WHEN NEW.identity_status = 'confirmed' AND NEW.active_fingerprint_id IS NULL
               BEGIN SELECT RAISE(ABORT, 'confirmed topic requires active fingerprint'); END"""
        )
        conn.execute(
            """INSERT INTO news_topics(id, category, name, first_seen_at, last_seen_at)
               VALUES ('pending', 'sports', '待确认', '2026-01-01', '2026-01-01')"""
        )

    store.init()

    with store.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE news_topics SET identity_status = 'confirmed', active_fingerprint_id = 'missing' WHERE id = 'pending'"
            )


def test_sqlite_event_migration_has_explicit_transaction_boundary():
    migration = (ROOT / "sql" / "upgrade_pna_news_event_aggregation_sqlite.sql").read_text(encoding="utf-8")

    assert migration.lstrip().startswith("-- One-time SQLite migration")
    assert "BEGIN IMMEDIATE;" in migration
    assert migration.rstrip().endswith("COMMIT;")
