BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN classification_version
    SET DEFAULT 'message-taxonomy-v4';

INSERT INTO schema_migrations(version)
VALUES ('073_update_message_taxonomy_v4')
ON CONFLICT (version) DO NOTHING;

COMMIT;
