BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN classification_version
    SET DEFAULT 'message-taxonomy-v2';

ALTER TABLE normalized_items
    ALTER COLUMN importance_policy_version
    SET DEFAULT 'importance-v10-community-promotion';

INSERT INTO schema_migrations(version)
VALUES ('060_add_community_promotion_type')
ON CONFLICT (version) DO NOTHING;

COMMIT;
