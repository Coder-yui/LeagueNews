BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN ontology_version SET DEFAULT 'lol-news-v4';

INSERT INTO schema_migrations(version)
VALUES ('050_update_event_identity_policy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
