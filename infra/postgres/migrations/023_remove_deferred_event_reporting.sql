BEGIN;

DROP TABLE IF EXISTS generated_reports;
DROP TABLE IF EXISTS event_revisions;
DROP TABLE IF EXISTS event_items;
DROP TABLE IF EXISTS news_events;

INSERT INTO schema_migrations(version)
VALUES ('023_remove_deferred_event_reporting')
ON CONFLICT (version) DO NOTHING;

COMMIT;
