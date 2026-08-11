BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN classification_version
    SET DEFAULT 'message-taxonomy-v3',
    ALTER COLUMN analysis_version
    SET DEFAULT 'message-processing-v1.1';

INSERT INTO schema_migrations(version)
VALUES ('062_update_message_taxonomy_v3')
ON CONFLICT (version) DO NOTHING;

COMMIT;
