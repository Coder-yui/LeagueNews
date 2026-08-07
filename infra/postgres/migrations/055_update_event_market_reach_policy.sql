BEGIN;

ALTER TABLE events
    ALTER COLUMN importance_policy_version
        SET DEFAULT 'event-importance-v4-market-reach';

INSERT INTO schema_migrations(version)
VALUES ('055_update_event_market_reach_policy')
ON CONFLICT (version) DO NOTHING;

COMMIT;
