BEGIN;

-- Databases initialized from ORM metadata before the message taxonomy migration
-- can lack the server defaults that migration 037 established on upgraded
-- databases. Keep the legacy columns non-null for backward compatibility while
-- allowing the current message projection to omit them.
ALTER TABLE normalized_items
    ALTER COLUMN primary_topic SET DEFAULT 'other',
    ALTER COLUMN secondary_topics SET DEFAULT '[]'::jsonb;

INSERT INTO schema_migrations(version)
VALUES ('057_restore_legacy_normalized_defaults')
ON CONFLICT (version) DO NOTHING;

COMMIT;
