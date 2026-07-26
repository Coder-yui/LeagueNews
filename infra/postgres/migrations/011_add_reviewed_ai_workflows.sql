BEGIN;

CREATE TABLE IF NOT EXISTS processing_runs (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL REFERENCES raw_items(id) ON DELETE CASCADE,
    workflow_type varchar(40) NOT NULL,
    status varchar(40) NOT NULL DEFAULT 'running',
    current_stage varchar(40) NOT NULL,
    context json NOT NULL DEFAULT '{}'::json,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_processing_runs_raw_item_id ON processing_runs(raw_item_id);
CREATE INDEX IF NOT EXISTS ix_processing_runs_workflow_type ON processing_runs(workflow_type);
CREATE INDEX IF NOT EXISTS ix_processing_runs_status ON processing_runs(status);
CREATE INDEX IF NOT EXISTS ix_processing_runs_current_stage ON processing_runs(current_stage);

CREATE TABLE IF NOT EXISTS review_tasks (
    id serial PRIMARY KEY,
    processing_run_id integer NOT NULL REFERENCES processing_runs(id) ON DELETE CASCADE,
    stage varchar(40) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'pending',
    proposal json NOT NULL DEFAULT '{}'::json,
    feedback json NOT NULL DEFAULT '{}'::json,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_review_tasks_processing_run_id
    ON review_tasks(processing_run_id);
CREATE INDEX IF NOT EXISTS ix_review_tasks_stage ON review_tasks(stage);
CREATE INDEX IF NOT EXISTS ix_review_tasks_status ON review_tasks(status);

CREATE TABLE IF NOT EXISTS knowledge_rules (
    id serial PRIMARY KEY,
    knowledge_type varchar(40) NOT NULL,
    scope varchar(160) NOT NULL DEFAULT 'global',
    rule_text text NOT NULL,
    correction_data json NOT NULL DEFAULT '{}'::json,
    source_review_id integer REFERENCES review_tasks(id) ON DELETE SET NULL,
    version integer NOT NULL DEFAULT 1,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_knowledge_type
    ON knowledge_rules(knowledge_type);
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_scope ON knowledge_rules(scope);
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_source_review_id
    ON knowledge_rules(source_review_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_rules_is_active ON knowledge_rules(is_active);

CREATE TABLE IF NOT EXISTS glossary_terms (
    id serial PRIMARY KEY,
    source_term varchar(255) NOT NULL,
    preferred_translation varchar(255) NOT NULL,
    forbidden_translations json NOT NULL DEFAULT '[]'::json,
    scope varchar(160) NOT NULL DEFAULT 'lol',
    notes text,
    source_review_id integer REFERENCES review_tasks(id) ON DELETE SET NULL,
    version integer NOT NULL DEFAULT 1,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_glossary_terms_source_term ON glossary_terms(source_term);
CREATE INDEX IF NOT EXISTS ix_glossary_terms_scope ON glossary_terms(scope);
CREATE INDEX IF NOT EXISTS ix_glossary_terms_source_review_id
    ON glossary_terms(source_review_id);
CREATE INDEX IF NOT EXISTS ix_glossary_terms_is_active ON glossary_terms(is_active);

ALTER TABLE normalized_items
    ADD COLUMN IF NOT EXISTS event_status varchar(30) NOT NULL DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS ix_normalized_items_event_status
    ON normalized_items(event_status);

ALTER TABLE news_events
    ADD COLUMN IF NOT EXISTS event_type varchar(60) NOT NULL DEFAULT 'other';
ALTER TABLE news_events
    ADD COLUMN IF NOT EXISTS status varchar(30) NOT NULL DEFAULT 'active';
ALTER TABLE news_events
    ADD COLUMN IF NOT EXISTS primary_item_id integer
        REFERENCES normalized_items(id) ON DELETE SET NULL;
ALTER TABLE news_events
    ADD COLUMN IF NOT EXISTS first_published_at timestamptz;
ALTER TABLE news_events
    ADD COLUMN IF NOT EXISTS last_activity_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_news_events_event_type ON news_events(event_type);
CREATE INDEX IF NOT EXISTS ix_news_events_status ON news_events(status);
CREATE INDEX IF NOT EXISTS ix_news_events_first_published_at
    ON news_events(first_published_at);
CREATE INDEX IF NOT EXISTS ix_news_events_last_activity_at
    ON news_events(last_activity_at);

UPDATE news_events e
SET first_published_at = COALESCE(e.first_published_at, e.occurred_at, e.created_at),
    last_activity_at = COALESCE(e.last_activity_at, e.occurred_at, e.created_at),
    primary_item_id = COALESCE(
        e.primary_item_id,
        (
            SELECT ei.normalized_item_id
            FROM event_items ei
            WHERE ei.event_id = e.id
            ORDER BY ei.is_primary DESC, ei.created_at ASC
            LIMIT 1
        )
    );

CREATE TABLE IF NOT EXISTS event_revisions (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
    version integer NOT NULL,
    change_type varchar(40) NOT NULL,
    snapshot json NOT NULL,
    source_review_id integer REFERENCES review_tasks(id) ON DELETE SET NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(event_id, version)
);
CREATE INDEX IF NOT EXISTS ix_event_revisions_event_id ON event_revisions(event_id);

CREATE TABLE IF NOT EXISTS generated_reports (
    id serial PRIMARY KEY,
    report_type varchar(20) NOT NULL,
    timezone varchar(80) NOT NULL DEFAULT 'Asia/Shanghai',
    period_start timestamptz NOT NULL,
    period_end timestamptz NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'pending_review',
    title varchar(500) NOT NULL,
    content text NOT NULL,
    source_event_ids json NOT NULL DEFAULT '[]'::json,
    source_revision_ids json NOT NULL DEFAULT '[]'::json,
    generation_context json NOT NULL DEFAULT '{}'::json,
    model_name varchar(120) NOT NULL,
    review_feedback json NOT NULL DEFAULT '{}'::json,
    created_at timestamptz NOT NULL DEFAULT now(),
    approved_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_generated_reports_report_type
    ON generated_reports(report_type);
CREATE INDEX IF NOT EXISTS ix_generated_reports_status ON generated_reports(status);
CREATE INDEX IF NOT EXISTS ix_generated_reports_period_start
    ON generated_reports(period_start);
CREATE INDEX IF NOT EXISTS ix_generated_reports_period_end
    ON generated_reports(period_end);

INSERT INTO schema_migrations(version)
SELECT '011_add_reviewed_ai_workflows'
WHERE NOT EXISTS (
    SELECT 1 FROM schema_migrations WHERE version = '011_add_reviewed_ai_workflows'
);

COMMIT;
