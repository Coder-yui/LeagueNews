BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'importance-v6-free-skin-rewards';

INSERT INTO schema_migrations(version)
VALUES ('052_update_free_skin_reward_policy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
