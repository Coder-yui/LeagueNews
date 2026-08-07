BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN ontology_version SET DEFAULT 'lol-news-v5';

ALTER TABLE events
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'event-importance-v3-impact-only';

INSERT INTO schema_migrations(version)
VALUES ('051_update_event_mentions_policy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
