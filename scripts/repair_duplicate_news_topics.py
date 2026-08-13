from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from personal_news_agent.core.text import stable_id
from personal_news_agent.services.event_identity import confirmed_event_key
from personal_news_agent.services.store import NewsStore


APPOINTMENT_ACTIONS = {"出任", "担任", "就任", "任命"}


def find_repairs(store: NewsStore) -> list[dict[str, Any]]:
    """Return only high-confidence deterministic duplicate pairs."""
    with store.connect() as conn:
        confirmed_rows = conn.execute(
            """SELECT t.id, t.first_seen_at, f.normalized_payload_json
               FROM news_topics t JOIN news_event_fingerprints f ON f.id = t.active_fingerprint_id
               WHERE t.status = 'active' AND t.identity_status = 'confirmed'
                 AND f.status = 'active' AND f.key_kind = 'confirmed'
               ORDER BY t.first_seen_at, t.id"""
        ).fetchall()
        grouped: dict[tuple[str, str, str | None], list[dict[str, Any]]] = {}
        for row in confirmed_rows:
            payload = json.loads(row["normalized_payload_json"])
            action = payload.get("action_key")
            if action not in APPOINTMENT_ACTIONS:
                continue
            key = (payload.get("subject_key") or "", payload.get("object_key") or "", payload.get("temporal_scope"))
            if not all(key[:2]):
                continue
            grouped.setdefault(key, []).append({"id": row["id"], "first_seen_at": row["first_seen_at"]})
        repairs: list[dict[str, Any]] = []
        for topics in grouped.values():
            if len(topics) < 2:
                continue
            primary = topics[0]
            for duplicate in topics[1:]:
                article_ids = [
                    row["article_id"]
                    for row in conn.execute(
                        "SELECT article_id FROM news_topic_articles WHERE topic_id = ? ORDER BY article_id",
                        (duplicate["id"],),
                    ).fetchall()
                ]
                repairs.append(
                    {
                        "primary_topic_id": primary["id"],
                        "duplicate_topic_id": duplicate["id"],
                        "article_ids": article_ids,
                        "reason": "same confirmed subject and object with equivalent appointment action",
                        "confidence": 0.99,
                    }
                )
        legacy_rows = conn.execute(
            """SELECT t.id, t.category, COALESCE(t.canonical_name, t.name) AS name,
                      t.article_count, t.first_seen_at,
                      GROUP_CONCAT(DISTINCT ta.subject) AS subjects
               FROM news_topics t
               LEFT JOIN news_event_fingerprints f ON f.id = t.active_fingerprint_id
               LEFT JOIN news_topic_articles ta ON ta.topic_id = t.id
               WHERE t.status = 'active' AND t.identity_status = 'pending'
                 AND (t.active_fingerprint_id IS NULL OR f.algorithm_version = 'pending-legacy-topic/v1')
               GROUP BY t.id
               ORDER BY t.article_count DESC, t.first_seen_at, t.id"""
        ).fetchall()
        legacy_groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in legacy_rows:
            identity = _legacy_appointment_identity(row["name"], row["subjects"] or "")
            if identity:
                legacy_groups.setdefault(identity, []).append(row)
        for topics in legacy_groups.values():
            if len(topics) < 2:
                continue
            primary = topics[0]
            for duplicate in topics[1:]:
                article_ids = [
                    row["article_id"]
                    for row in conn.execute(
                        "SELECT article_id FROM news_topic_articles WHERE topic_id = ? ORDER BY article_id",
                        (duplicate["id"],),
                    ).fetchall()
                ]
                repairs.append(
                    {
                        "primary_topic_id": primary["id"],
                        "duplicate_topic_id": duplicate["id"],
                        "article_ids": article_ids,
                        "reason": "same explicit subject and equivalent short-track coaching appointment event",
                        "confidence": 0.98,
                        "confirmed_fingerprint": {
                            "subject": "武大靖",
                            "action": "出任",
                            "object": "中国短道速滑队主教练",
                            "temporal_scope": None,
                        },
                    }
                )
    return repairs


def _legacy_appointment_identity(name: str, subjects: str) -> tuple[str, str] | None:
    title = "".join(str(name or "").split())
    subject_values = {value.strip() for value in str(subjects or "").split(",") if value.strip()}
    if "武大靖" not in subject_values and "武大靖" not in title:
        return None
    appointment = any(token in title for token in ("出任", "担任", "就任", "执教"))
    short_track_team = "短道速滑" in title and any(token in title for token in ("主教练", "教练", "队"))
    if not appointment or not short_track_team:
        return None
    return "武大靖", "中国短道速滑队主教练"


def apply_repairs(store: NewsStore, repairs: list[dict[str, Any]]) -> dict[str, int]:
    result = {"merged": 0, "moved_articles": 0, "skipped": 0, "conflicts": 0, "failed": 0}
    if not repairs:
        return result
    attempt_id = stable_id("attempt", f"historical-repair:{_now()}")
    with store.connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        for repair in repairs:
            primary_id = repair["primary_topic_id"]
            duplicate_id = repair["duplicate_topic_id"]
            primary = conn.execute("SELECT * FROM news_topics WHERE id = ? AND status = 'active'", (primary_id,)).fetchone()
            duplicate = conn.execute(
                "SELECT * FROM news_topics WHERE id = ? AND status = 'active'",
                (duplicate_id,),
            ).fetchone()
            if not primary or not duplicate:
                result["skipped"] += 1
                continue
            if primary["identity_status"] != "confirmed":
                payload = repair.get("confirmed_fingerprint")
                if not payload:
                    result["skipped"] += 1
                    continue
                fingerprint = confirmed_event_key(
                    subject=payload["subject"],
                    action=payload["action"],
                    object_=payload["object"],
                    temporal_scope=payload.get("temporal_scope"),
                )
                existing = conn.execute(
                    """SELECT topic_id FROM news_event_fingerprints
                       WHERE algorithm_version = ? AND fingerprint_hash = ?
                         AND key_kind = 'confirmed' AND status = 'active'""",
                    (fingerprint.algorithm_version, fingerprint.fingerprint_hash),
                ).fetchone()
                if existing and existing["topic_id"] != primary_id:
                    result["conflicts"] += 1
                    continue
                _ensure_legacy_alias(store, conn, primary_id)
                conn.execute(
                    "UPDATE news_event_fingerprints SET status = 'superseded', superseded_at = ? WHERE topic_id = ? AND status = 'active'",
                    (_now(), primary_id),
                )
                fingerprint_id = store._insert_event_fingerprint(conn, primary_id, fingerprint)
                conn.execute(
                    "UPDATE news_topics SET active_fingerprint_id = ?, identity_status = 'confirmed' WHERE id = ?",
                    (fingerprint_id, primary_id),
                )
            _ensure_legacy_alias(store, conn, duplicate_id)
            rows = conn.execute(
                "SELECT article_id FROM news_topic_articles WHERE topic_id = ? ORDER BY article_id",
                (duplicate_id,),
            ).fetchall()
            conn.execute("UPDATE news_topic_articles SET topic_id = ? WHERE topic_id = ?", (primary_id, duplicate_id))
            conn.execute(
                "UPDATE news_topics SET status = 'merged', merged_into_topic_id = ?, projection_state = 'dirty' WHERE id = ?",
                (primary_id, duplicate_id),
            )
            store._recalculate_event_topic(conn, primary_id)
            store._append_topics_merged_summary(conn, primary_id, duplicate)
            for row in rows:
                store._insert_classification_audit(
                    conn,
                    attempt_id,
                    row["article_id"],
                    {primary_id, duplicate_id},
                    duplicate_id,
                    primary_id,
                    "reclassified",
                    store._active_fingerprint_in_conn(conn, primary_id),
                    {},
                    repair["confidence"],
                    "rule",
                    "historical_duplicate_repair",
                    repair["reason"],
                )
            result["merged"] += 1
            result["moved_articles"] += len(rows)
        conn.execute(
            """INSERT INTO operation_logs(id, operation, status, target, detail_json, created_at)
               VALUES (?, 'repair_duplicate_news_topics', 'ok', 'historical', ?, ?)""",
            (
                stable_id("log", f"repair:{attempt_id}"),
                json.dumps(result, ensure_ascii=False),
                _now(),
            ),
        )
    return result


def _ensure_legacy_alias(store: NewsStore, conn, topic_id: str) -> None:
    existing = conn.execute(
        "SELECT id FROM news_event_fingerprints WHERE topic_id = ? ORDER BY created_at LIMIT 1", (topic_id,)
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE news_topics SET active_fingerprint_id = COALESCE(active_fingerprint_id, ?) WHERE id = ?",
            (existing["id"], topic_id),
        )
        return
    from personal_news_agent.services.event_identity import pending_legacy_topic_event_key

    fingerprint_id = store._insert_event_fingerprint(conn, topic_id, pending_legacy_topic_event_key(topic_id))
    conn.execute("UPDATE news_topics SET active_fingerprint_id = ? WHERE id = ?", (fingerprint_id, topic_id))


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or repair high-confidence duplicate canonical news events.")
    parser.add_argument("--database", type=Path, default=ROOT / "personal_news.db")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only (default).")
    mode.add_argument("--apply", action="store_true", help="Apply all reported repairs in one transaction.")
    args = parser.parse_args()

    if not args.database.is_file():
        parser.error(f"database does not exist: {args.database}")

    store = NewsStore(args.database)
    repairs = find_repairs(store)
    output: dict[str, Any] = {"mode": "apply" if args.apply else "dry-run", "database": str(args.database), "repairs": repairs}
    if args.apply:
        output["result"] = apply_repairs(store, repairs)
    else:
        output["result"] = {"proposed_merges": len(repairs), "proposed_articles": sum(len(item["article_ids"]) for item in repairs)}
    print(json.dumps(output, ensure_ascii=False, indent=2))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


if __name__ == "__main__":
    main()
