BEGIN;

CREATE TABLE events (
    id serial PRIMARY KEY,
    event_key varchar(160) UNIQUE,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    category varchar(60) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'active',
    first_published_at timestamptz,
    last_published_at timestamptz,
    current_revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_events_current_revision_positive CHECK (current_revision >= 1),
    CONSTRAINT ck_events_publish_range CHECK (
        first_published_at IS NULL
        OR last_published_at IS NULL
        OR first_published_at <= last_published_at
    )
);
CREATE INDEX ix_events_category ON events(category);
CREATE INDEX ix_events_status ON events(status);
CREATE INDEX ix_events_first_published_at ON events(first_published_at);
CREATE INDEX ix_events_last_published_at ON events(last_published_at);

CREATE TABLE event_messages (
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE RESTRICT,
    relation_type varchar(30) NOT NULL DEFAULT 'primary',
    source_published_at timestamptz,
    added_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, normalized_item_id),
    CONSTRAINT uq_event_messages_normalized_item UNIQUE (normalized_item_id),
    CONSTRAINT ck_event_messages_relation_type CHECK (relation_type = 'primary')
);
CREATE INDEX ix_event_messages_source_published_at
    ON event_messages(source_published_at);

CREATE TABLE event_revisions (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    change_note text NOT NULL,
    evidence_snapshot json NOT NULL DEFAULT '{}'::json,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_event_revisions_event_revision UNIQUE (event_id, revision),
    CONSTRAINT ck_event_revisions_revision_positive CHECK (revision >= 1)
);
CREATE INDEX ix_event_revisions_event_id ON event_revisions(event_id);

INSERT INTO schema_migrations(version)
VALUES ('026_create_event_aggregation_v2')
ON CONFLICT (version) DO NOTHING;

COMMIT;
