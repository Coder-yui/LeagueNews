BEGIN;

ALTER TABLE normalized_items
    ALTER COLUMN ontology_version SET DEFAULT 'lol-news-v3',
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'importance-v5-controlled-subtype';

ALTER TABLE events
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'event-importance-v2-member-led';

INSERT INTO schema_migrations(version)
VALUES ('049_update_processing_policy_defaults')
ON CONFLICT (version) DO NOTHING;

COMMIT;
