-- One-time SQLite migration for canonical news events.
-- Run on a backup first. Existing topics remain pending until explicitly repaired.

BEGIN IMMEDIATE;

ALTER TABLE news_topics ADD COLUMN canonical_name TEXT;
ALTER TABLE news_topics ADD COLUMN primary_category TEXT;
ALTER TABLE news_topics ADD COLUMN category_scope_json TEXT DEFAULT '[]';
ALTER TABLE news_topics ADD COLUMN event_date TEXT;
ALTER TABLE news_topics ADD COLUMN identity_status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE news_topics ADD COLUMN merged_into_topic_id TEXT;
ALTER TABLE news_topics ADD COLUMN active_fingerprint_id TEXT;
ALTER TABLE news_topics ADD COLUMN summary_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE news_topics ADD COLUMN projection_state TEXT NOT NULL DEFAULT 'dirty';

ALTER TABLE news_topic_articles ADD COLUMN event_stage TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE news_topic_articles ADD COLUMN stage_label TEXT;
ALTER TABLE news_topic_articles ADD COLUMN article_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE news_topic_articles ADD COLUMN classification_reason TEXT;
ALTER TABLE news_topic_articles ADD COLUMN classification_source TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE news_topic_articles ADD COLUMN classified_at TEXT;

ALTER TABLE topic_clusters ADD COLUMN projection_version TEXT;
ALTER TABLE topic_clusters ADD COLUMN projected_at TEXT;

ALTER TABLE event_timelines ADD COLUMN topic_id TEXT;
ALTER TABLE event_timelines ADD COLUMN article_id TEXT;
ALTER TABLE event_timelines ADD COLUMN event_stage TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE event_timelines ADD COLUMN article_type TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE event_timelines ADD COLUMN projection_version TEXT;
ALTER TABLE event_timelines ADD COLUMN projected_at TEXT;

CREATE TABLE news_event_fingerprints (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL,
  key_kind TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  normalized_payload_json TEXT NOT NULL,
  fingerprint_hash TEXT NOT NULL,
  event_key TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  superseded_at TEXT,
  FOREIGN KEY(topic_id) REFERENCES news_topics(id)
);
CREATE UNIQUE INDEX idx_event_fingerprints_active_confirmed
ON news_event_fingerprints(algorithm_version, fingerprint_hash)
WHERE status = 'active' AND key_kind = 'confirmed';
CREATE UNIQUE INDEX idx_event_fingerprints_topic_version
ON news_event_fingerprints(topic_id, algorithm_version)
WHERE status = 'active' AND key_kind = 'confirmed';

CREATE TABLE news_topic_summary_revisions (
  id TEXT PRIMARY KEY,
  topic_id TEXT NOT NULL,
  revision INTEGER NOT NULL,
  summary TEXT NOT NULL,
  source_article_ids_json TEXT NOT NULL,
  stage_snapshot_json TEXT NOT NULL,
  generation_source TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(topic_id, revision),
  FOREIGN KEY(topic_id) REFERENCES news_topics(id)
);

CREATE TABLE news_event_classification_audits (
  id TEXT PRIMARY KEY,
  attempt_id TEXT NOT NULL,
  article_id TEXT NOT NULL,
  candidate_topic_ids_json TEXT NOT NULL,
  from_topic_id TEXT,
  to_topic_id TEXT,
  decision TEXT NOT NULL,
  event_key TEXT,
  algorithm_version TEXT,
  event_stage TEXT,
  article_type TEXT,
  confidence REAL,
  classification_source TEXT,
  reason_code TEXT,
  reason_text TEXT,
  model_key TEXT,
  schema_version TEXT,
  created_at TEXT NOT NULL
);

-- Keep the newest legacy row before enforcing idempotent timeline projection.
DELETE FROM event_timelines
WHERE topic_id IS NOT NULL AND article_id IS NOT NULL AND rowid NOT IN (
  SELECT MAX(rowid) FROM event_timelines
  WHERE topic_id IS NOT NULL AND article_id IS NOT NULL
  GROUP BY topic_id, article_id
);
CREATE UNIQUE INDEX idx_event_timelines_topic_article
ON event_timelines(topic_id, article_id);

CREATE TRIGGER trg_news_topics_confirmed_requires_fingerprint_insert
BEFORE INSERT ON news_topics WHEN NEW.identity_status = 'confirmed' AND NOT EXISTS (
  SELECT 1 FROM news_event_fingerprints f
  WHERE f.id = NEW.active_fingerprint_id AND f.topic_id = NEW.id
    AND f.key_kind = 'confirmed' AND f.status = 'active'
)
BEGIN SELECT RAISE(ABORT, 'confirmed topic requires active fingerprint'); END;

CREATE TRIGGER trg_news_topics_confirmed_requires_fingerprint_update
BEFORE UPDATE OF identity_status, active_fingerprint_id ON news_topics
WHEN NEW.identity_status = 'confirmed' AND NOT EXISTS (
  SELECT 1 FROM news_event_fingerprints f
  WHERE f.id = NEW.active_fingerprint_id AND f.topic_id = NEW.id
    AND f.key_kind = 'confirmed' AND f.status = 'active'
)
BEGIN SELECT RAISE(ABORT, 'confirmed topic requires active fingerprint'); END;

CREATE TRIGGER trg_event_fingerprint_protect_confirmed_update
BEFORE UPDATE ON news_event_fingerprints
WHEN EXISTS (
  SELECT 1 FROM news_topics t
  WHERE t.active_fingerprint_id = OLD.id AND t.identity_status = 'confirmed'
) AND (
  NEW.id != OLD.id OR NEW.topic_id != OLD.topic_id OR NEW.key_kind != OLD.key_kind
  OR NEW.algorithm_version != OLD.algorithm_version
  OR NEW.normalized_payload_json != OLD.normalized_payload_json
  OR NEW.fingerprint_hash != OLD.fingerprint_hash OR NEW.event_key != OLD.event_key
  OR NEW.status != OLD.status
)
BEGIN SELECT RAISE(ABORT, 'active confirmed fingerprint is referenced'); END;

CREATE TRIGGER trg_event_fingerprint_protect_confirmed_delete
BEFORE DELETE ON news_event_fingerprints
WHEN EXISTS (
  SELECT 1 FROM news_topics t
  WHERE t.active_fingerprint_id = OLD.id AND t.identity_status = 'confirmed'
)
BEGIN SELECT RAISE(ABORT, 'active confirmed fingerprint is referenced'); END;

COMMIT;
