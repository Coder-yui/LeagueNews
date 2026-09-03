BEGIN;

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v integer PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    parent_checkpoint_id text,
    type text,
    checkpoint jsonb NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    channel text NOT NULL,
    version text NOT NULL,
    type text NOT NULL,
    blob bytea,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id text NOT NULL,
    checkpoint_ns text NOT NULL DEFAULT '',
    checkpoint_id text NOT NULL,
    task_id text NOT NULL,
    task_path text NOT NULL DEFAULT '',
    idx integer NOT NULL,
    channel text NOT NULL,
    type text,
    blob bytea NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx
    ON checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx
    ON checkpoint_blobs(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx
    ON checkpoint_writes(thread_id);

INSERT INTO checkpoint_migrations(v)
SELECT value
FROM generate_series(0, 9) AS value
ON CONFLICT (v) DO NOTHING;

ALTER TABLE processing_runs
    DROP CONSTRAINT ck_processing_runs_outcome,
    ADD CONSTRAINT ck_processing_runs_outcome CHECK (
        outcome IS NULL OR outcome IN (
            'approved', 'irrelevant', 'insufficient_evidence',
            'review_rejected', 'system_error', 'correction_requested',
            'raw_item_superseded'
        )
    );

INSERT INTO schema_migrations(version)
VALUES ('076_add_langgraph_checkpoint_store')
ON CONFLICT (version) DO NOTHING;

COMMIT;
