BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN importance_policy_version
    SET DEFAULT 'importance-v11-repost-weekly-rotation';

INSERT INTO schema_migrations(version)
VALUES ('061_update_importance_policy_v11')
ON CONFLICT (version) DO NOTHING;

COMMIT;
