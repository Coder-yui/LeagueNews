BEGIN;

-- event-aggregation-v1 is a clean replacement, not an upgrade of the retired
-- event implementation. This deliberately removes only the retired event
-- projection and its unused workflow links. Source evidence and the complete
-- message pipeline remain untouched.
ALTER TABLE knowledge_rules
    DROP COLUMN IF EXISTS source_event_review_id;
ALTER TABLE processing_checkpoints
    DROP COLUMN IF EXISTS event_aggregation_run_id;
ALTER TABLE pipeline_jobs
    DROP COLUMN IF EXISTS event_aggregation_run_id;
ALTER TABLE pipeline_corrections
    DROP COLUMN IF EXISTS source_event_run_id,
    DROP COLUMN IF EXISTS original_event_ids;

DROP TABLE IF EXISTS event_claims;
DROP TABLE IF EXISTS event_review_tasks;
DROP TABLE IF EXISTS event_messages;
DROP TABLE IF EXISTS event_revisions;
DROP TABLE IF EXISTS events;
DROP TABLE IF EXISTS event_aggregation_runs;

CREATE TABLE events (
    id serial PRIMARY KEY,
    aggregation_key varchar(255) UNIQUE,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    event_family varchar(50) NOT NULL,
    products jsonb NOT NULL DEFAULT '[]'::jsonb,
    canonical_anchors jsonb NOT NULL DEFAULT '{}'::jsonb,
    latest_development text NOT NULL DEFAULT '',
    key_facts jsonb NOT NULL DEFAULT '[]'::jsonb,
    unresolved_points jsonb NOT NULL DEFAULT '[]'::jsonb,
    lifecycle_status varchar(40) NOT NULL DEFAULT 'developing',
    first_seen_at timestamptz,
    last_seen_at timestamptz,
    last_material_update_at timestamptz,
    importance_score double precision NOT NULL DEFAULT 0,
    importance_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    importance_policy_version varchar(80) NOT NULL DEFAULT 'event-importance-v1',
    credibility_score double precision NOT NULL DEFAULT 0,
    credibility_level varchar(40) NOT NULL DEFAULT 'unverified',
    credibility_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    credibility_policy_version varchar(80) NOT NULL DEFAULT 'event-credibility-v1',
    independent_source_count integer NOT NULL DEFAULT 0,
    supporting_source_count integer NOT NULL DEFAULT 0,
    contradicting_source_count integer NOT NULL DEFAULT 0,
    official_source_count integer NOT NULL DEFAULT 0,
    heat_score double precision NOT NULL DEFAULT 0,
    heat_calculated_at timestamptz,
    heat_breakdown jsonb NOT NULL DEFAULT '{}'::jsonb,
    heat_policy_version varchar(80) NOT NULL DEFAULT 'event-heat-v1',
    message_count_total integer NOT NULL DEFAULT 0,
    message_count_24h integer NOT NULL DEFAULT 0,
    unique_sources_24h integer NOT NULL DEFAULT 0,
    origin_message_id integer REFERENCES normalized_items(id) ON DELETE SET NULL,
    primary_source_message_id integer REFERENCES normalized_items(id) ON DELETE SET NULL,
    latest_update_message_id integer REFERENCES normalized_items(id) ON DELETE SET NULL,
    best_media_message_id integer REFERENCES normalized_items(id) ON DELETE SET NULL,
    aggregation_policy_version varchar(80) NOT NULL DEFAULT 'event-aggregation-v1',
    current_revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_events_current_revision_positive CHECK (current_revision >= 1),
    CONSTRAINT ck_events_family CHECK (
        event_family IN (
            'gameplay_balance',
            'gameplay_release',
            'cosmetic_release',
            'player_activity',
            'commercial_offer',
            'service_incident',
            'security_enforcement',
            'esports_match',
            'esports_schedule',
            'roster_change',
            'esports_rules',
            'universe_release',
            'media_release',
            'corporate_change',
            'platform_service',
            'other_named_development'
        )
    ),
    CONSTRAINT ck_events_lifecycle CHECK (
        lifecycle_status IN (
            'unconfirmed',
            'developing',
            'confirmed',
            'disputed',
            'denied',
            'resolved',
            'stale'
        )
    ),
    CONSTRAINT ck_events_credibility_level CHECK (
        credibility_level IN (
            'unverified',
            'plausible',
            'corroborated',
            'officially_confirmed',
            'disputed',
            'denied'
        )
    ),
    CONSTRAINT ck_events_credibility_score CHECK (
        credibility_score >= 0 AND credibility_score <= 1
    ),
    CONSTRAINT ck_events_importance_score CHECK (
        importance_score >= 0 AND importance_score <= 1
    ),
    CONSTRAINT ck_events_heat_score CHECK (heat_score >= 0 AND heat_score <= 1),
    CONSTRAINT ck_events_evidence_counts CHECK (
        independent_source_count >= 0
        AND supporting_source_count >= 0
        AND contradicting_source_count >= 0
        AND official_source_count >= 0
    ),
    CONSTRAINT ck_events_message_counts CHECK (
        message_count_total >= 0
        AND message_count_24h >= 0
        AND unique_sources_24h >= 0
    ),
    CONSTRAINT ck_events_publish_range CHECK (
        first_seen_at IS NULL
        OR last_seen_at IS NULL
        OR first_seen_at <= last_seen_at
    )
);

CREATE INDEX ix_events_event_family ON events(event_family);
CREATE INDEX ix_events_lifecycle_status ON events(lifecycle_status);
CREATE INDEX ix_events_last_seen_at ON events(last_seen_at);
CREATE INDEX ix_events_last_material_update_at ON events(last_material_update_at);
CREATE INDEX ix_events_importance_score ON events(importance_score);
CREATE INDEX ix_events_credibility_level ON events(credibility_level);
CREATE INDEX ix_events_heat_score ON events(heat_score);

CREATE TABLE event_mentions (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE RESTRICT,
    normalized_item_revision integer NOT NULL,
    mention_index integer NOT NULL,
    aggregation_policy_version varchar(80) NOT NULL DEFAULT 'event-aggregation-v1',
    relation varchar(30) NOT NULL DEFAULT 'reports',
    source_role varchar(40) NOT NULL DEFAULT 'unknown',
    independence_group varchar(500),
    materiality varchar(30) NOT NULL DEFAULT 'material_update',
    evidence_excerpt text NOT NULL DEFAULT '',
    structured_fact_changes jsonb NOT NULL DEFAULT '{}'::jsonb,
    impact_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_fingerprint varchar(64),
    source_reliability_snapshot double precision NOT NULL DEFAULT 0.5,
    source_published_at timestamptz,
    added_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_event_mentions_item_index_policy UNIQUE (
        normalized_item_id,
        normalized_item_revision,
        mention_index,
        aggregation_policy_version
    ),
    CONSTRAINT ck_event_mentions_item_revision CHECK (normalized_item_revision >= 1),
    CONSTRAINT ck_event_mentions_index_nonnegative CHECK (mention_index >= 0),
    CONSTRAINT ck_event_mentions_relation CHECK (
        relation IN ('reports', 'supports', 'confirms', 'denies', 'corrects', 'mentions')
    ),
    CONSTRAINT ck_event_mentions_source_role CHECK (
        source_role IN (
            'responsible_official',
            'direct_subject',
            'first_party_participant',
            'independent_media',
            'known_leaker',
            'ordinary_account',
            'republisher',
            'unknown'
        )
    ),
    CONSTRAINT ck_event_mentions_materiality CHECK (
        materiality IN (
            'material_update',
            'corroboration_only',
            'duplicate',
            'context_only'
        )
    ),
    CONSTRAINT ck_event_mentions_source_reliability CHECK (
        source_reliability_snapshot >= 0 AND source_reliability_snapshot <= 1
    )
);

CREATE INDEX ix_event_mentions_event_id ON event_mentions(event_id);
CREATE INDEX ix_event_mentions_normalized_item_id ON event_mentions(normalized_item_id);
CREATE INDEX ix_event_mentions_source_published_at ON event_mentions(source_published_at);
CREATE INDEX ix_event_mentions_independence_group
    ON event_mentions(event_id, independence_group);

CREATE TABLE event_revisions (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    change_note text NOT NULL,
    evidence_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_event_revisions_event_revision UNIQUE (event_id, revision),
    CONSTRAINT ck_event_revisions_revision_positive CHECK (revision >= 1)
);

CREATE INDEX ix_event_revisions_event_id ON event_revisions(event_id);

CREATE TABLE event_aggregation_runs (
    id serial PRIMARY KEY,
    normalized_item_id integer NOT NULL
        REFERENCES normalized_items(id) ON DELETE RESTRICT,
    normalized_item_revision integer NOT NULL,
    status varchar(40) NOT NULL DEFAULT 'running',
    outcome varchar(40),
    current_stage varchar(40) NOT NULL DEFAULT 'admission',
    admission_decision varchar(30),
    aggregation_policy_version varchar(80) NOT NULL DEFAULT 'event-aggregation-v1',
    idempotency_key varchar(255) NOT NULL UNIQUE,
    input_fingerprint varchar(64),
    model_call_count integer NOT NULL DEFAULT 0,
    candidate_snapshot jsonb NOT NULL DEFAULT '[]'::jsonb,
    decision_draft jsonb NOT NULL DEFAULT '{}'::jsonb,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz,
    applied_at timestamptz,
    CONSTRAINT ck_event_runs_item_revision CHECK (normalized_item_revision >= 1),
    CONSTRAINT ck_event_runs_model_call_count CHECK (model_call_count >= 0)
);

CREATE INDEX ix_event_aggregation_runs_normalized_item_id
    ON event_aggregation_runs(normalized_item_id);
CREATE INDEX ix_event_aggregation_runs_status ON event_aggregation_runs(status);
CREATE UNIQUE INDEX uq_event_aggregation_runs_active_item
    ON event_aggregation_runs(normalized_item_id)
    WHERE status IN ('running', 'awaiting_review');

INSERT INTO schema_migrations(version)
VALUES ('063_replace_event_system_with_v1')
ON CONFLICT (version) DO NOTHING;

COMMIT;
