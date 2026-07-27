BEGIN;

CREATE TABLE source_collection_schedules (
    id serial PRIMARY KEY,
    source_id integer NOT NULL
        REFERENCES sources(id) ON DELETE CASCADE,
    enabled boolean NOT NULL DEFAULT false,
    interval_minutes integer NOT NULL DEFAULT 60,
    retry_delay_minutes integer NOT NULL DEFAULT 15,
    fetch_limit integer NOT NULL DEFAULT 10,
    options json NOT NULL DEFAULT '{}'::json,
    next_run_at timestamptz,
    run_requested_at timestamptz,
    last_started_at timestamptz,
    last_finished_at timestamptz,
    last_success_at timestamptz,
    last_connector_run_id integer
        REFERENCES connector_runs(id) ON DELETE SET NULL,
    last_status varchar(30) NOT NULL DEFAULT 'idle',
    last_error text,
    lease_token varchar(64),
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_source_collection_schedules_source_id UNIQUE (source_id),
    CONSTRAINT ck_source_collection_schedules_interval
        CHECK (interval_minutes >= 5 AND interval_minutes <= 10080),
    CONSTRAINT ck_source_collection_schedules_retry_delay
        CHECK (retry_delay_minutes >= 1 AND retry_delay_minutes <= 1440),
    CONSTRAINT ck_source_collection_schedules_fetch_limit
        CHECK (fetch_limit >= 1 AND fetch_limit <= 50),
    CONSTRAINT ck_source_collection_schedules_status
        CHECK (last_status IN ('idle', 'running', 'succeeded', 'failed'))
);

CREATE INDEX ix_source_collection_schedules_source_id
    ON source_collection_schedules(source_id);
CREATE INDEX ix_source_collection_schedules_enabled
    ON source_collection_schedules(enabled);
CREATE INDEX ix_source_collection_schedules_next_run_at
    ON source_collection_schedules(next_run_at);
CREATE INDEX ix_source_collection_schedules_run_requested_at
    ON source_collection_schedules(run_requested_at);
CREATE INDEX ix_source_collection_schedules_last_status
    ON source_collection_schedules(last_status);
CREATE INDEX ix_source_collection_schedules_lease_token
    ON source_collection_schedules(lease_token);
CREATE INDEX ix_source_collection_schedules_lease_expires_at
    ON source_collection_schedules(lease_expires_at);

INSERT INTO schema_migrations(version)
VALUES ('031_add_source_collection_schedules')
ON CONFLICT (version) DO NOTHING;

COMMIT;
