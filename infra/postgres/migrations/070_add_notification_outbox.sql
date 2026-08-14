BEGIN;

CREATE TABLE IF NOT EXISTS notification_outbox (
    id serial PRIMARY KEY,
    target varchar(30) NOT NULL,
    kind varchar(50) NOT NULL,
    dedupe_key varchar(255) NOT NULL UNIQUE,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar(20) NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL DEFAULT now(),
    last_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    sent_at timestamptz,
    lease_token varchar(64),
    lease_expires_at timestamptz,
    CONSTRAINT ck_notification_outbox_target
        CHECK (target IN ('featured', 'alert')),
    CONSTRAINT ck_notification_outbox_kind
        CHECK (kind IN ('featured_message', 'collection_failure', 'pipeline_failure')),
    CONSTRAINT ck_notification_outbox_status
        CHECK (status IN ('pending', 'sending', 'sent', 'failed')),
    CONSTRAINT ck_notification_outbox_attempts CHECK (attempts >= 0)
);

CREATE INDEX IF NOT EXISTS ix_notification_outbox_target ON notification_outbox(target);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_kind ON notification_outbox(kind);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_status ON notification_outbox(status);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_next_attempt_at
    ON notification_outbox(next_attempt_at);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_lease_token
    ON notification_outbox(lease_token);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_lease_expires_at
    ON notification_outbox(lease_expires_at);
CREATE INDEX IF NOT EXISTS ix_notification_outbox_claimable
    ON notification_outbox(status, next_attempt_at);

INSERT INTO schema_migrations(version)
VALUES ('070_add_notification_outbox')
ON CONFLICT (version) DO NOTHING;

COMMIT;
