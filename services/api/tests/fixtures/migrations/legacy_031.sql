CREATE TABLE schema_migrations (
    version varchar(100) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO schema_migrations(version) VALUES
('002_content_pipeline_v2'),
('003_fix_article_media_order'),
('004_add_translation_fields'),
('005_fix_plain_text_from_blocks'),
('006_add_media_extractions'),
('007_add_connector_ingestion'),
('008_seed_web_connector_sources'),
('009_source_identity_per_publisher'),
('010_add_weibo_tieba_sources'),
('011_add_reviewed_ai_workflows'),
('012_track_approved_media_extractions'),
('013_add_ocr_lab'),
('014_add_patch_table_structure'),
('015_raw_items_content_blocks_v2'),
('016_normalize_legacy_embed_urls'),
('017_simplify_raw_item_identity'),
('018_normalize_x_author_names'),
('019_version_raw_item_ingestion'),
('020_reset_processing_data'),
('021_remove_legacy_raw_item_identity_index'),
('022_refine_reviewed_item_pipeline'),
('023_remove_deferred_event_reporting'),
('024_restore_production_ocr_profile'),
('025_reset_item_processing_state'),
('026_create_event_aggregation_v2'),
('027_add_event_review_workflow'),
('028_add_event_editorial_metrics'),
('029_add_pipeline_corrections'),
('030_add_automatic_pipeline_jobs'),
('031_add_source_collection_schedules');

CREATE TABLE sources (
    id serial PRIMARY KEY,
    name varchar(255) NOT NULL UNIQUE,
    connector_type varchar(60) NOT NULL,
    external_key varchar(255),
    base_url varchar(1000),
    connector_config json NOT NULL DEFAULT '{}'::json,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE source_collection_schedules (
    id serial PRIMARY KEY,
    source_id integer NOT NULL UNIQUE,
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
    last_connector_run_id integer,
    last_status varchar(30) NOT NULL DEFAULT 'idle',
    last_error text,
    lease_token varchar(64),
    lease_expires_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE connector_runs (
    id serial PRIMARY KEY,
    source_id integer NOT NULL,
    connector_type varchar(60) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'running',
    discovered_count integer NOT NULL DEFAULT 0,
    created_count integer NOT NULL DEFAULT 0,
    revised_count integer NOT NULL DEFAULT 0,
    skipped_count integer NOT NULL DEFAULT 0,
    error_message text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);

CREATE TABLE pipeline_jobs (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL,
    correction_id integer,
    status varchar(30) NOT NULL DEFAULT 'queued',
    current_stage varchar(40) NOT NULL DEFAULT 'relevance',
    processing_run_id integer,
    event_aggregation_run_id integer,
    last_checkpoint_id integer,
    attempts integer NOT NULL DEFAULT 0,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    started_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE processing_runs (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL,
    supersedes_run_id integer,
    workflow_type varchar(40) NOT NULL,
    status varchar(40) NOT NULL DEFAULT 'running',
    outcome varchar(40),
    current_stage varchar(40) NOT NULL,
    execution_mode varchar(20) NOT NULL DEFAULT 'manual',
    correction_id integer,
    restart_from_stage varchar(40),
    context json NOT NULL DEFAULT '{}'::json,
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    completed_at timestamptz
);

CREATE TABLE review_tasks (
    id serial PRIMARY KEY,
    processing_run_id integer NOT NULL,
    stage varchar(40) NOT NULL,
    status varchar(30) NOT NULL DEFAULT 'pending',
    proposal json NOT NULL DEFAULT '{}'::json,
    feedback json NOT NULL DEFAULT '{}'::json,
    decision_source varchar(20) NOT NULL DEFAULT 'manual',
    policy_version varchar(80),
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz
);

CREATE TABLE knowledge_rules (
    id serial PRIMARY KEY,
    knowledge_type varchar(40) NOT NULL,
    scope varchar(160) NOT NULL DEFAULT 'global',
    rule_text text NOT NULL,
    correction_data json NOT NULL DEFAULT '{}'::json,
    source_review_id integer,
    source_event_review_id integer,
    version integer NOT NULL DEFAULT 1,
    is_active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE media_assets (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL,
    block_index integer NOT NULL,
    source_url varchar(1000),
    storage_path varchar(1000),
    mime_type varchar(120),
    sha256 varchar(64),
    width integer,
    height integer,
    alt_text varchar(500),
    caption text,
    ocr_text text,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE raw_items (
    id serial PRIMARY KEY,
    source_id integer NOT NULL,
    external_id varchar(255),
    revision integer NOT NULL DEFAULT 1,
    content_blocks json NOT NULL DEFAULT '[]'::json
);

CREATE TABLE normalized_items (
    id serial PRIMARY KEY,
    raw_item_id integer NOT NULL UNIQUE,
    normalized_title varchar(500) NOT NULL,
    normalized_text text NOT NULL,
    summary text NOT NULL,
    category varchar(60) NOT NULL,
    entities json NOT NULL DEFAULT '[]'::json,
    importance_score double precision NOT NULL,
    credibility varchar(30) NOT NULL,
    credibility_score double precision NOT NULL,
    credibility_evidence json NOT NULL DEFAULT '[]'::json,
    language varchar(30),
    source_language varchar(30),
    target_language varchar(30) NOT NULL DEFAULT 'zh-CN',
    translated_title varchar(500),
    translated_text text,
    translated_content_blocks json NOT NULL DEFAULT '[]'::json,
    translation_status varchar(30) NOT NULL DEFAULT 'pending',
    translation_model varchar(120),
    analysis_model varchar(120) NOT NULL,
    analysis_version varchar(30) NOT NULL DEFAULT 'v2',
    current_revision integer NOT NULL DEFAULT 1,
    publication_status varchar(30) NOT NULL DEFAULT 'published',
    withdrawn_at timestamptz,
    withdrawal_reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_normalized_items_publication_status
        CHECK (publication_status IN ('published', 'withdrawn'))
);

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
    event_type varchar(40) NOT NULL DEFAULT 'other',
    lifecycle_status varchar(40) NOT NULL DEFAULT 'developing',
    credibility_status varchar(40) NOT NULL DEFAULT 'unverified',
    credibility_score double precision NOT NULL DEFAULT 0,
    importance_score double precision NOT NULL DEFAULT 0,
    importance_evidence json NOT NULL DEFAULT '[]'::json,
    latest_development text NOT NULL DEFAULT '',
    independent_source_count integer NOT NULL DEFAULT 0,
    official_source_count integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pipeline_corrections (
    id serial PRIMARY KEY,
    event_id integer,
    restart_from_stage varchar(40) NOT NULL,
    CONSTRAINT ck_pipeline_corrections_restart_stage
        CHECK (
            restart_from_stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'item_analysis',
                'event_decision'
            )
        )
);

CREATE TABLE processing_checkpoints (
    id serial PRIMARY KEY,
    stage varchar(40) NOT NULL,
    CONSTRAINT ck_processing_checkpoints_stage
        CHECK (
            stage IN (
                'relevance',
                'image_ocr',
                'translation',
                'item_analysis',
                'event_decision'
            )
        )
);

CREATE TABLE event_messages (
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    normalized_item_id integer NOT NULL,
    relation_type varchar(30) NOT NULL DEFAULT 'primary',
    source_published_at timestamptz,
    added_at timestamptz NOT NULL DEFAULT now(),
    evidence_stance varchar(20) NOT NULL DEFAULT 'supports',
    independence_key varchar(255),
    is_official_confirmation boolean NOT NULL DEFAULT false,
    is_significant_update boolean NOT NULL DEFAULT true,
    membership_status varchar(20) NOT NULL DEFAULT 'active',
    withdrawn_at timestamptz,
    withdrawal_reason text,
    source_correction_id integer,
    PRIMARY KEY (event_id, normalized_item_id),
    CONSTRAINT ck_event_messages_evidence_stance
        CHECK (evidence_stance IN ('supports', 'contradicts', 'context'))
);

CREATE TABLE event_revisions (
    id serial PRIMARY KEY,
    event_id integer NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    revision integer NOT NULL,
    title varchar(500) NOT NULL,
    summary text NOT NULL,
    change_note text NOT NULL,
    evidence_snapshot json NOT NULL DEFAULT '{}'::json,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ocr_profiles (
    id serial PRIMARY KEY,
    name varchar(120) NOT NULL,
    parameters json NOT NULL,
    source_test_run_id integer,
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
