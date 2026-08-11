BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN importance_policy_version
    SET DEFAULT 'importance-v9-classification-native';

INSERT INTO schema_migrations(version)
VALUES ('059_update_importance_policy_v9')
ON CONFLICT (version) DO NOTHING;

COMMIT;
