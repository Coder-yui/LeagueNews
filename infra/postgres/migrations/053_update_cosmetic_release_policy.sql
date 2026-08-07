BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN ontology_version SET DEFAULT 'lol-news-v6',
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'importance-v7-cosmetic-releases';

INSERT INTO schema_migrations(version)
VALUES ('053_update_cosmetic_release_policy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
