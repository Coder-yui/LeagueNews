BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN importance_policy_version
    SET DEFAULT 'importance-v3-editorial-baselines';

INSERT INTO schema_migrations(version)
VALUES ('046_update_importance_policy_default')
ON CONFLICT (version) DO NOTHING;

COMMIT;
