--
-- PostgreSQL database dump
--

-- Dumped from database version 17.10
-- Dumped by pg_dump version 17.10

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: connector_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.connector_runs (
    id integer NOT NULL,
    source_id integer NOT NULL,
    connector_type character varying(60) NOT NULL,
    status character varying(30) DEFAULT 'running'::character varying NOT NULL,
    discovered_count integer DEFAULT 0 NOT NULL,
    created_count integer DEFAULT 0 NOT NULL,
    skipped_count integer DEFAULT 0 NOT NULL,
    error_message text,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    revised_count integer DEFAULT 0 NOT NULL
);


--
-- Name: connector_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.connector_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: connector_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.connector_runs_id_seq OWNED BY public.connector_runs.id;


--
-- Name: event_aggregation_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_aggregation_runs (
    id integer NOT NULL,
    normalized_item_id integer NOT NULL,
    supersedes_run_id integer,
    status character varying(40) DEFAULT 'running'::character varying NOT NULL,
    outcome character varying(40),
    current_stage character varying(40) DEFAULT 'event_decision'::character varying NOT NULL,
    candidate_snapshot json DEFAULT '[]'::json NOT NULL,
    decision_draft json DEFAULT '{}'::json NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    execution_mode character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    correction_id integer,
    restart_from_stage character varying(40)
);


--
-- Name: event_aggregation_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_aggregation_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_aggregation_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_aggregation_runs_id_seq OWNED BY public.event_aggregation_runs.id;


--
-- Name: event_messages; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_messages (
    event_id integer NOT NULL,
    normalized_item_id integer NOT NULL,
    relation_type character varying(30) DEFAULT 'primary'::character varying NOT NULL,
    source_published_at timestamp with time zone,
    added_at timestamp with time zone DEFAULT now() NOT NULL,
    evidence_stance character varying(20) DEFAULT 'supports'::character varying NOT NULL,
    independence_key character varying(255),
    is_official_confirmation boolean DEFAULT false NOT NULL,
    is_significant_update boolean DEFAULT true NOT NULL,
    membership_status character varying(20) DEFAULT 'active'::character varying NOT NULL,
    withdrawn_at timestamp with time zone,
    withdrawal_reason text,
    source_correction_id integer,
    CONSTRAINT ck_event_messages_evidence_stance CHECK (((evidence_stance)::text = ANY ((ARRAY['supports'::character varying, 'contradicts'::character varying, 'context'::character varying])::text[]))),
    CONSTRAINT ck_event_messages_membership_status CHECK (((membership_status)::text = ANY ((ARRAY['active'::character varying, 'withdrawn'::character varying])::text[]))),
    CONSTRAINT ck_event_messages_relation_type CHECK (((relation_type)::text = 'primary'::text))
);


--
-- Name: event_review_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_review_tasks (
    id integer NOT NULL,
    event_aggregation_run_id integer NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    proposal json DEFAULT '{}'::json NOT NULL,
    feedback json DEFAULT '{}'::json NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    decision_source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    policy_version character varying(80)
);


--
-- Name: event_review_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_review_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_review_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_review_tasks_id_seq OWNED BY public.event_review_tasks.id;


--
-- Name: event_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.event_revisions (
    id integer NOT NULL,
    event_id integer NOT NULL,
    revision integer NOT NULL,
    title character varying(500) NOT NULL,
    summary text NOT NULL,
    change_note text NOT NULL,
    evidence_snapshot json DEFAULT '{}'::json NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_event_revisions_revision_positive CHECK ((revision >= 1))
);


--
-- Name: event_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.event_revisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: event_revisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.event_revisions_id_seq OWNED BY public.event_revisions.id;


--
-- Name: events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.events (
    id integer NOT NULL,
    event_key character varying(160),
    title character varying(500) NOT NULL,
    summary text NOT NULL,
    category character varying(60) NOT NULL,
    status character varying(30) DEFAULT 'active'::character varying NOT NULL,
    first_published_at timestamp with time zone,
    last_published_at timestamp with time zone,
    current_revision integer DEFAULT 1 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(40) DEFAULT 'other'::character varying NOT NULL,
    lifecycle_status character varying(40) DEFAULT 'developing'::character varying NOT NULL,
    credibility_status character varying(40) DEFAULT 'unverified'::character varying NOT NULL,
    credibility_score double precision DEFAULT 0 NOT NULL,
    importance_score double precision DEFAULT 0 NOT NULL,
    importance_evidence json DEFAULT '[]'::json NOT NULL,
    latest_development text DEFAULT ''::text NOT NULL,
    independent_source_count integer DEFAULT 0 NOT NULL,
    official_source_count integer DEFAULT 0 NOT NULL,
    CONSTRAINT ck_events_credibility_score CHECK (((credibility_score >= (0)::double precision) AND (credibility_score <= (1)::double precision))),
    CONSTRAINT ck_events_current_revision_positive CHECK ((current_revision >= 1)),
    CONSTRAINT ck_events_importance_score CHECK (((importance_score >= (0)::double precision) AND (importance_score <= (1)::double precision))),
    CONSTRAINT ck_events_independent_source_count CHECK ((independent_source_count >= 0)),
    CONSTRAINT ck_events_official_source_count CHECK ((official_source_count >= 0)),
    CONSTRAINT ck_events_publish_range CHECK (((first_published_at IS NULL) OR (last_published_at IS NULL) OR (first_published_at <= last_published_at)))
);


--
-- Name: events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.events_id_seq OWNED BY public.events.id;


--
-- Name: glossary_terms; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.glossary_terms (
    id integer NOT NULL,
    source_term character varying(255) NOT NULL,
    preferred_translation character varying(255) NOT NULL,
    forbidden_translations json DEFAULT '[]'::json NOT NULL,
    scope character varying(160) DEFAULT 'lol'::character varying NOT NULL,
    notes text,
    source_review_id integer,
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: glossary_terms_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.glossary_terms_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: glossary_terms_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.glossary_terms_id_seq OWNED BY public.glossary_terms.id;


--
-- Name: knowledge_rules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.knowledge_rules (
    id integer NOT NULL,
    knowledge_type character varying(40) NOT NULL,
    scope character varying(160) DEFAULT 'global'::character varying NOT NULL,
    rule_text text NOT NULL,
    correction_data json DEFAULT '{}'::json NOT NULL,
    source_review_id integer,
    version integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_event_review_id integer
);


--
-- Name: knowledge_rules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.knowledge_rules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: knowledge_rules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.knowledge_rules_id_seq OWNED BY public.knowledge_rules.id;


--
-- Name: media_assets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_assets (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    block_index integer NOT NULL,
    source_url character varying(1000),
    storage_path character varying(1000),
    mime_type character varying(120),
    sha256 character varying(64),
    width integer,
    height integer,
    alt_text character varying(500),
    caption text,
    ocr_text text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: media_assets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.media_assets_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: media_assets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.media_assets_id_seq OWNED BY public.media_assets.id;


--
-- Name: media_extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.media_extractions (
    id integer NOT NULL,
    media_asset_id integer NOT NULL,
    task_type character varying(60) NOT NULL,
    provider character varying(120) NOT NULL,
    ocr_engine character varying(120) NOT NULL,
    structuring_model character varying(120) NOT NULL,
    schema_version character varying(30) NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    raw_ocr_text text NOT NULL,
    ocr_lines json DEFAULT '[]'::json NOT NULL,
    structured_data json DEFAULT '{}'::json NOT NULL,
    confidence double precision,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    processing_config json DEFAULT '{}'::json NOT NULL
);


--
-- Name: media_extractions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.media_extractions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: media_extractions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.media_extractions_id_seq OWNED BY public.media_extractions.id;


--
-- Name: normalized_item_media_extractions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.normalized_item_media_extractions (
    normalized_item_id integer NOT NULL,
    media_extraction_id integer NOT NULL,
    translated_structured_data json DEFAULT '{}'::json NOT NULL,
    translation_status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    translation_model character varying(120),
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: normalized_item_revisions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.normalized_item_revisions (
    id integer NOT NULL,
    normalized_item_id integer NOT NULL,
    revision integer NOT NULL,
    snapshot json DEFAULT '{}'::json NOT NULL,
    processing_run_id integer,
    change_note text DEFAULT 'published'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_normalized_item_revisions_revision_positive CHECK ((revision >= 1))
);


--
-- Name: normalized_item_revisions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.normalized_item_revisions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: normalized_item_revisions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.normalized_item_revisions_id_seq OWNED BY public.normalized_item_revisions.id;


--
-- Name: normalized_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.normalized_items (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    normalized_title character varying(500) NOT NULL,
    normalized_text text NOT NULL,
    summary text NOT NULL,
    category character varying(60) NOT NULL,
    entities json DEFAULT '[]'::json NOT NULL,
    importance_score double precision NOT NULL,
    credibility character varying(30) NOT NULL,
    language character varying(30),
    analysis_model character varying(120) NOT NULL,
    analysis_version character varying(30) DEFAULT 'v2'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    source_language character varying(30),
    target_language character varying(30) DEFAULT 'zh-CN'::character varying NOT NULL,
    translated_title character varying(500),
    translated_text text,
    translated_content_blocks json DEFAULT '[]'::json NOT NULL,
    translation_status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    translation_model character varying(120),
    credibility_score double precision NOT NULL,
    credibility_evidence json DEFAULT '[]'::json NOT NULL,
    current_revision integer DEFAULT 1 NOT NULL,
    publication_status character varying(30) DEFAULT 'published'::character varying NOT NULL,
    withdrawn_at timestamp with time zone,
    withdrawal_reason text,
    CONSTRAINT ck_normalized_items_current_revision_positive CHECK ((current_revision >= 1)),
    CONSTRAINT ck_normalized_items_publication_status CHECK (((publication_status)::text = ANY ((ARRAY['published'::character varying, 'withdrawn'::character varying])::text[])))
);


--
-- Name: normalized_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.normalized_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: normalized_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.normalized_items_id_seq OWNED BY public.normalized_items.id;


--
-- Name: ocr_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_profiles (
    id integer NOT NULL,
    name character varying(120) NOT NULL,
    parameters json NOT NULL,
    source_test_run_id integer,
    is_active boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: ocr_profiles_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ocr_profiles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ocr_profiles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ocr_profiles_id_seq OWNED BY public.ocr_profiles.id;


--
-- Name: ocr_test_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ocr_test_runs (
    id integer NOT NULL,
    media_asset_id integer NOT NULL,
    profile_name character varying(120) NOT NULL,
    parameters json NOT NULL,
    status character varying(30) DEFAULT 'completed'::character varying NOT NULL,
    raw_text text NOT NULL,
    lines json DEFAULT '[]'::json NOT NULL,
    confidence double precision NOT NULL,
    source_width integer NOT NULL,
    source_height integer NOT NULL,
    processed_width integer NOT NULL,
    processed_height integer NOT NULL,
    overlay_path character varying(1000),
    engine character varying(120) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    table_overlay_path character varying(1000),
    table_data json DEFAULT '{}'::json NOT NULL,
    structure_confidence double precision
);


--
-- Name: ocr_test_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.ocr_test_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: ocr_test_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.ocr_test_runs_id_seq OWNED BY public.ocr_test_runs.id;


--
-- Name: pipeline_corrections; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_corrections (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    normalized_item_id integer,
    event_id integer,
    source_processing_run_id integer,
    source_event_run_id integer,
    checkpoint_id integer,
    restart_from_stage character varying(40) NOT NULL,
    resume_mode character varying(20) NOT NULL,
    reason text NOT NULL,
    status character varying(30) DEFAULT 'requested'::character varying NOT NULL,
    error_message text,
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    completed_at timestamp with time zone,
    CONSTRAINT ck_pipeline_corrections_restart_stage CHECK (((restart_from_stage)::text = ANY ((ARRAY['relevance'::character varying, 'image_ocr'::character varying, 'translation'::character varying, 'item_analysis'::character varying, 'event_decision'::character varying])::text[]))),
    CONSTRAINT ck_pipeline_corrections_resume_mode CHECK (((resume_mode)::text = ANY ((ARRAY['manual'::character varying, 'automatic'::character varying])::text[]))),
    CONSTRAINT ck_pipeline_corrections_status CHECK (((status)::text = ANY ((ARRAY['requested'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: pipeline_corrections_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_corrections_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_corrections_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_corrections_id_seq OWNED BY public.pipeline_corrections.id;


--
-- Name: pipeline_jobs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pipeline_jobs (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    correction_id integer,
    status character varying(30) DEFAULT 'queued'::character varying NOT NULL,
    current_stage character varying(40) DEFAULT 'relevance'::character varying NOT NULL,
    processing_run_id integer,
    event_aggregation_run_id integer,
    last_checkpoint_id integer,
    attempts integer DEFAULT 0 NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    started_at timestamp with time zone,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT ck_pipeline_jobs_attempts CHECK ((attempts >= 0)),
    CONSTRAINT ck_pipeline_jobs_status CHECK (((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying, 'completed'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
);


--
-- Name: pipeline_jobs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.pipeline_jobs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: pipeline_jobs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.pipeline_jobs_id_seq OWNED BY public.pipeline_jobs.id;


--
-- Name: processing_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_checkpoints (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    normalized_item_id integer,
    processing_run_id integer,
    event_aggregation_run_id integer,
    correction_id integer,
    stage character varying(40) NOT NULL,
    output_snapshot json DEFAULT '{}'::json NOT NULL,
    artifact_references json DEFAULT '{}'::json NOT NULL,
    knowledge_snapshot json DEFAULT '{}'::json NOT NULL,
    model_name character varying(120),
    decision_source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    invalidated_at timestamp with time zone,
    invalidation_reason text,
    CONSTRAINT ck_processing_checkpoints_decision_source CHECK (((decision_source)::text = ANY ((ARRAY['manual'::character varying, 'automatic'::character varying, 'system'::character varying])::text[]))),
    CONSTRAINT ck_processing_checkpoints_stage CHECK (((stage)::text = ANY ((ARRAY['relevance'::character varying, 'image_ocr'::character varying, 'translation'::character varying, 'item_analysis'::character varying, 'event_decision'::character varying])::text[])))
);


--
-- Name: processing_checkpoints_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.processing_checkpoints_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: processing_checkpoints_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.processing_checkpoints_id_seq OWNED BY public.processing_checkpoints.id;


--
-- Name: processing_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.processing_runs (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    workflow_type character varying(40) NOT NULL,
    status character varying(40) DEFAULT 'running'::character varying NOT NULL,
    current_stage character varying(40) NOT NULL,
    context json DEFAULT '{}'::json NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    supersedes_run_id integer,
    outcome character varying(40),
    execution_mode character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    correction_id integer,
    restart_from_stage character varying(40)
);


--
-- Name: processing_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.processing_runs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: processing_runs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.processing_runs_id_seq OWNED BY public.processing_runs.id;


--
-- Name: raw_item_source_payloads; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_item_source_payloads (
    id integer NOT NULL,
    raw_item_id integer NOT NULL,
    provider character varying(50) NOT NULL,
    payload json DEFAULT '{}'::json NOT NULL,
    captured_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: raw_item_source_payloads_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_item_source_payloads_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_item_source_payloads_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_item_source_payloads_id_seq OWNED BY public.raw_item_source_payloads.id;


--
-- Name: raw_items; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.raw_items (
    id integer NOT NULL,
    source_id integer NOT NULL,
    external_id character varying(255),
    canonical_url character varying(1000),
    native_title character varying(500),
    published_at timestamp with time zone,
    ingested_at timestamp with time zone DEFAULT now() NOT NULL,
    author_name character varying(255),
    language character varying(30),
    content_blocks json DEFAULT '[]'::json NOT NULL,
    content_hash character varying(64),
    content_kind character varying(30) DEFAULT 'post'::character varying NOT NULL,
    content_hash_version integer DEFAULT 1 NOT NULL,
    revision integer DEFAULT 1 NOT NULL,
    supersedes_raw_item_id integer
);


--
-- Name: raw_items_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.raw_items_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: raw_items_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.raw_items_id_seq OWNED BY public.raw_items.id;


--
-- Name: review_tasks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.review_tasks (
    id integer NOT NULL,
    processing_run_id integer NOT NULL,
    stage character varying(40) NOT NULL,
    status character varying(30) DEFAULT 'pending'::character varying NOT NULL,
    proposal json DEFAULT '{}'::json NOT NULL,
    feedback json DEFAULT '{}'::json NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    resolved_at timestamp with time zone,
    decision_source character varying(20) DEFAULT 'manual'::character varying NOT NULL,
    policy_version character varying(80)
);


--
-- Name: review_tasks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.review_tasks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: review_tasks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.review_tasks_id_seq OWNED BY public.review_tasks.id;


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying(100) NOT NULL,
    applied_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: source_collection_schedules; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.source_collection_schedules (
    id integer NOT NULL,
    source_id integer NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    interval_minutes integer DEFAULT 60 NOT NULL,
    retry_delay_minutes integer DEFAULT 15 NOT NULL,
    fetch_limit integer DEFAULT 10 NOT NULL,
    options json DEFAULT '{}'::json NOT NULL,
    next_run_at timestamp with time zone,
    run_requested_at timestamp with time zone,
    last_started_at timestamp with time zone,
    last_finished_at timestamp with time zone,
    last_success_at timestamp with time zone,
    last_connector_run_id integer,
    last_status character varying(30) DEFAULT 'idle'::character varying NOT NULL,
    last_error text,
    lease_token character varying(64),
    lease_expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_source_collection_schedules_fetch_limit CHECK (((fetch_limit >= 1) AND (fetch_limit <= 50))),
    CONSTRAINT ck_source_collection_schedules_interval CHECK (((interval_minutes >= 5) AND (interval_minutes <= 10080))),
    CONSTRAINT ck_source_collection_schedules_retry_delay CHECK (((retry_delay_minutes >= 1) AND (retry_delay_minutes <= 1440))),
    CONSTRAINT ck_source_collection_schedules_status CHECK (((last_status)::text = ANY ((ARRAY['idle'::character varying, 'running'::character varying, 'succeeded'::character varying, 'failed'::character varying])::text[])))
);


--
-- Name: source_collection_schedules_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.source_collection_schedules_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: source_collection_schedules_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.source_collection_schedules_id_seq OWNED BY public.source_collection_schedules.id;


--
-- Name: sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sources (
    id integer NOT NULL,
    name character varying(120) NOT NULL,
    connector_type character varying(60) DEFAULT 'manual'::character varying NOT NULL,
    base_url character varying(500),
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    external_key character varying(255),
    connector_config json DEFAULT '{}'::json NOT NULL
);


--
-- Name: sources_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sources_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sources_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sources_id_seq OWNED BY public.sources.id;


--
-- Name: connector_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_runs ALTER COLUMN id SET DEFAULT nextval('public.connector_runs_id_seq'::regclass);


--
-- Name: event_aggregation_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_aggregation_runs ALTER COLUMN id SET DEFAULT nextval('public.event_aggregation_runs_id_seq'::regclass);


--
-- Name: event_review_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_review_tasks ALTER COLUMN id SET DEFAULT nextval('public.event_review_tasks_id_seq'::regclass);


--
-- Name: event_revisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_revisions ALTER COLUMN id SET DEFAULT nextval('public.event_revisions_id_seq'::regclass);


--
-- Name: events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events ALTER COLUMN id SET DEFAULT nextval('public.events_id_seq'::regclass);


--
-- Name: glossary_terms id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_terms ALTER COLUMN id SET DEFAULT nextval('public.glossary_terms_id_seq'::regclass);


--
-- Name: knowledge_rules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules ALTER COLUMN id SET DEFAULT nextval('public.knowledge_rules_id_seq'::regclass);


--
-- Name: media_assets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets ALTER COLUMN id SET DEFAULT nextval('public.media_assets_id_seq'::regclass);


--
-- Name: media_extractions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_extractions ALTER COLUMN id SET DEFAULT nextval('public.media_extractions_id_seq'::regclass);


--
-- Name: normalized_item_revisions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_revisions ALTER COLUMN id SET DEFAULT nextval('public.normalized_item_revisions_id_seq'::regclass);


--
-- Name: normalized_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_items ALTER COLUMN id SET DEFAULT nextval('public.normalized_items_id_seq'::regclass);


--
-- Name: ocr_profiles id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_profiles ALTER COLUMN id SET DEFAULT nextval('public.ocr_profiles_id_seq'::regclass);


--
-- Name: ocr_test_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_test_runs ALTER COLUMN id SET DEFAULT nextval('public.ocr_test_runs_id_seq'::regclass);


--
-- Name: pipeline_corrections id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections ALTER COLUMN id SET DEFAULT nextval('public.pipeline_corrections_id_seq'::regclass);


--
-- Name: pipeline_jobs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs ALTER COLUMN id SET DEFAULT nextval('public.pipeline_jobs_id_seq'::regclass);


--
-- Name: processing_checkpoints id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints ALTER COLUMN id SET DEFAULT nextval('public.processing_checkpoints_id_seq'::regclass);


--
-- Name: processing_runs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_runs ALTER COLUMN id SET DEFAULT nextval('public.processing_runs_id_seq'::regclass);


--
-- Name: raw_item_source_payloads id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_item_source_payloads ALTER COLUMN id SET DEFAULT nextval('public.raw_item_source_payloads_id_seq'::regclass);


--
-- Name: raw_items id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_items ALTER COLUMN id SET DEFAULT nextval('public.raw_items_id_seq'::regclass);


--
-- Name: review_tasks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_tasks ALTER COLUMN id SET DEFAULT nextval('public.review_tasks_id_seq'::regclass);


--
-- Name: source_collection_schedules id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_collection_schedules ALTER COLUMN id SET DEFAULT nextval('public.source_collection_schedules_id_seq'::regclass);


--
-- Name: sources id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources ALTER COLUMN id SET DEFAULT nextval('public.sources_id_seq'::regclass);


--
-- Name: connector_runs connector_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_runs
    ADD CONSTRAINT connector_runs_pkey PRIMARY KEY (id);


--
-- Name: event_aggregation_runs event_aggregation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_aggregation_runs
    ADD CONSTRAINT event_aggregation_runs_pkey PRIMARY KEY (id);


--
-- Name: event_messages event_messages_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_messages
    ADD CONSTRAINT event_messages_pkey PRIMARY KEY (event_id, normalized_item_id);


--
-- Name: event_review_tasks event_review_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_review_tasks
    ADD CONSTRAINT event_review_tasks_pkey PRIMARY KEY (id);


--
-- Name: event_revisions event_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_revisions
    ADD CONSTRAINT event_revisions_pkey PRIMARY KEY (id);


--
-- Name: events events_event_key_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_event_key_key UNIQUE (event_key);


--
-- Name: events events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.events
    ADD CONSTRAINT events_pkey PRIMARY KEY (id);


--
-- Name: glossary_terms glossary_terms_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_terms
    ADD CONSTRAINT glossary_terms_pkey PRIMARY KEY (id);


--
-- Name: knowledge_rules knowledge_rules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules
    ADD CONSTRAINT knowledge_rules_pkey PRIMARY KEY (id);


--
-- Name: media_assets media_assets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_pkey PRIMARY KEY (id);


--
-- Name: media_extractions media_extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_extractions
    ADD CONSTRAINT media_extractions_pkey PRIMARY KEY (id);


--
-- Name: normalized_item_media_extractions normalized_item_media_extractions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_media_extractions
    ADD CONSTRAINT normalized_item_media_extractions_pkey PRIMARY KEY (normalized_item_id, media_extraction_id);


--
-- Name: normalized_item_revisions normalized_item_revisions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_revisions
    ADD CONSTRAINT normalized_item_revisions_pkey PRIMARY KEY (id);


--
-- Name: normalized_items normalized_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_items
    ADD CONSTRAINT normalized_items_pkey PRIMARY KEY (id);


--
-- Name: normalized_items normalized_items_raw_item_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_items
    ADD CONSTRAINT normalized_items_raw_item_id_key UNIQUE (raw_item_id);


--
-- Name: ocr_profiles ocr_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_profiles
    ADD CONSTRAINT ocr_profiles_pkey PRIMARY KEY (id);


--
-- Name: ocr_test_runs ocr_test_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_test_runs
    ADD CONSTRAINT ocr_test_runs_pkey PRIMARY KEY (id);


--
-- Name: pipeline_corrections pipeline_corrections_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_pkey PRIMARY KEY (id);


--
-- Name: pipeline_jobs pipeline_jobs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_pkey PRIMARY KEY (id);


--
-- Name: processing_checkpoints processing_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_pkey PRIMARY KEY (id);


--
-- Name: processing_runs processing_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_runs
    ADD CONSTRAINT processing_runs_pkey PRIMARY KEY (id);


--
-- Name: raw_item_source_payloads raw_item_source_payloads_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_item_source_payloads
    ADD CONSTRAINT raw_item_source_payloads_pkey PRIMARY KEY (id);


--
-- Name: raw_item_source_payloads raw_item_source_payloads_raw_item_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_item_source_payloads
    ADD CONSTRAINT raw_item_source_payloads_raw_item_id_key UNIQUE (raw_item_id);


--
-- Name: raw_items raw_items_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_items
    ADD CONSTRAINT raw_items_pkey PRIMARY KEY (id);


--
-- Name: review_tasks review_tasks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_tasks
    ADD CONSTRAINT review_tasks_pkey PRIMARY KEY (id);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: source_collection_schedules source_collection_schedules_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_collection_schedules
    ADD CONSTRAINT source_collection_schedules_pkey PRIMARY KEY (id);


--
-- Name: sources sources_name_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_name_key UNIQUE (name);


--
-- Name: sources sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sources
    ADD CONSTRAINT sources_pkey PRIMARY KEY (id);


--
-- Name: event_revisions uq_event_revisions_event_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_revisions
    ADD CONSTRAINT uq_event_revisions_event_revision UNIQUE (event_id, revision);


--
-- Name: normalized_item_revisions uq_normalized_item_revisions_item_revision; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_revisions
    ADD CONSTRAINT uq_normalized_item_revisions_item_revision UNIQUE (normalized_item_id, revision);


--
-- Name: source_collection_schedules uq_source_collection_schedules_source_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_collection_schedules
    ADD CONSTRAINT uq_source_collection_schedules_source_id UNIQUE (source_id);


--
-- Name: ix_connector_runs_connector_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_connector_runs_connector_type ON public.connector_runs USING btree (connector_type);


--
-- Name: ix_connector_runs_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_connector_runs_source_id ON public.connector_runs USING btree (source_id);


--
-- Name: ix_connector_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_connector_runs_status ON public.connector_runs USING btree (status);


--
-- Name: ix_event_aggregation_runs_correction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_aggregation_runs_correction_id ON public.event_aggregation_runs USING btree (correction_id);


--
-- Name: ix_event_aggregation_runs_execution_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_aggregation_runs_execution_mode ON public.event_aggregation_runs USING btree (execution_mode);


--
-- Name: ix_event_aggregation_runs_normalized_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_aggregation_runs_normalized_item_id ON public.event_aggregation_runs USING btree (normalized_item_id);


--
-- Name: ix_event_aggregation_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_aggregation_runs_status ON public.event_aggregation_runs USING btree (status);


--
-- Name: ix_event_aggregation_runs_supersedes_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_aggregation_runs_supersedes_run_id ON public.event_aggregation_runs USING btree (supersedes_run_id);


--
-- Name: ix_event_messages_independence_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_messages_independence_key ON public.event_messages USING btree (event_id, independence_key);


--
-- Name: ix_event_messages_membership_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_messages_membership_status ON public.event_messages USING btree (membership_status);


--
-- Name: ix_event_messages_source_correction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_messages_source_correction_id ON public.event_messages USING btree (source_correction_id);


--
-- Name: ix_event_messages_source_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_messages_source_published_at ON public.event_messages USING btree (source_published_at);


--
-- Name: ix_event_review_tasks_decision_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_review_tasks_decision_source ON public.event_review_tasks USING btree (decision_source);


--
-- Name: ix_event_review_tasks_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_review_tasks_run_id ON public.event_review_tasks USING btree (event_aggregation_run_id);


--
-- Name: ix_event_review_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_review_tasks_status ON public.event_review_tasks USING btree (status);


--
-- Name: ix_event_revisions_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_event_revisions_event_id ON public.event_revisions USING btree (event_id);


--
-- Name: ix_events_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_category ON public.events USING btree (category);


--
-- Name: ix_events_credibility_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_credibility_status ON public.events USING btree (credibility_status);


--
-- Name: ix_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_event_type ON public.events USING btree (event_type);


--
-- Name: ix_events_first_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_first_published_at ON public.events USING btree (first_published_at);


--
-- Name: ix_events_importance_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_importance_score ON public.events USING btree (importance_score);


--
-- Name: ix_events_last_published_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_last_published_at ON public.events USING btree (last_published_at);


--
-- Name: ix_events_lifecycle_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_lifecycle_status ON public.events USING btree (lifecycle_status);


--
-- Name: ix_events_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_events_status ON public.events USING btree (status);


--
-- Name: ix_glossary_terms_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_terms_is_active ON public.glossary_terms USING btree (is_active);


--
-- Name: ix_glossary_terms_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_terms_scope ON public.glossary_terms USING btree (scope);


--
-- Name: ix_glossary_terms_source_review_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_terms_source_review_id ON public.glossary_terms USING btree (source_review_id);


--
-- Name: ix_glossary_terms_source_term; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_glossary_terms_source_term ON public.glossary_terms USING btree (source_term);


--
-- Name: ix_knowledge_rules_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_is_active ON public.knowledge_rules USING btree (is_active);


--
-- Name: ix_knowledge_rules_knowledge_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_knowledge_type ON public.knowledge_rules USING btree (knowledge_type);


--
-- Name: ix_knowledge_rules_scope; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_scope ON public.knowledge_rules USING btree (scope);


--
-- Name: ix_knowledge_rules_source_event_review_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_source_event_review_id ON public.knowledge_rules USING btree (source_event_review_id);


--
-- Name: ix_knowledge_rules_source_review_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_knowledge_rules_source_review_id ON public.knowledge_rules USING btree (source_review_id);


--
-- Name: ix_media_assets_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_media_assets_raw_item_id ON public.media_assets USING btree (raw_item_id);


--
-- Name: ix_media_assets_sha256; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_media_assets_sha256 ON public.media_assets USING btree (sha256);


--
-- Name: ix_media_extractions_media_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_media_extractions_media_asset_id ON public.media_extractions USING btree (media_asset_id);


--
-- Name: ix_media_extractions_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_media_extractions_status ON public.media_extractions USING btree (status);


--
-- Name: ix_media_extractions_task_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_media_extractions_task_type ON public.media_extractions USING btree (task_type);


--
-- Name: ix_normalized_item_media_translation_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_item_media_translation_status ON public.normalized_item_media_extractions USING btree (translation_status);


--
-- Name: ix_normalized_item_revisions_normalized_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_item_revisions_normalized_item_id ON public.normalized_item_revisions USING btree (normalized_item_id);


--
-- Name: ix_normalized_item_revisions_processing_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_item_revisions_processing_run_id ON public.normalized_item_revisions USING btree (processing_run_id);


--
-- Name: ix_normalized_items_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_category ON public.normalized_items USING btree (category);


--
-- Name: ix_normalized_items_credibility; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_credibility ON public.normalized_items USING btree (credibility);


--
-- Name: ix_normalized_items_credibility_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_credibility_score ON public.normalized_items USING btree (credibility_score);


--
-- Name: ix_normalized_items_importance_score; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_importance_score ON public.normalized_items USING btree (importance_score);


--
-- Name: ix_normalized_items_publication_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_publication_status ON public.normalized_items USING btree (publication_status);


--
-- Name: ix_normalized_items_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX ix_normalized_items_raw_item_id ON public.normalized_items USING btree (raw_item_id);


--
-- Name: ix_normalized_items_translation_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_normalized_items_translation_status ON public.normalized_items USING btree (translation_status);


--
-- Name: ix_ocr_profiles_is_active; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ocr_profiles_is_active ON public.ocr_profiles USING btree (is_active);


--
-- Name: ix_ocr_test_runs_media_asset_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ocr_test_runs_media_asset_id ON public.ocr_test_runs USING btree (media_asset_id);


--
-- Name: ix_ocr_test_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_ocr_test_runs_status ON public.ocr_test_runs USING btree (status);


--
-- Name: ix_pipeline_corrections_checkpoint_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_checkpoint_id ON public.pipeline_corrections USING btree (checkpoint_id);


--
-- Name: ix_pipeline_corrections_event_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_event_id ON public.pipeline_corrections USING btree (event_id);


--
-- Name: ix_pipeline_corrections_normalized_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_normalized_item_id ON public.pipeline_corrections USING btree (normalized_item_id);


--
-- Name: ix_pipeline_corrections_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_raw_item_id ON public.pipeline_corrections USING btree (raw_item_id);


--
-- Name: ix_pipeline_corrections_restart_from_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_restart_from_stage ON public.pipeline_corrections USING btree (restart_from_stage);


--
-- Name: ix_pipeline_corrections_resume_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_resume_mode ON public.pipeline_corrections USING btree (resume_mode);


--
-- Name: ix_pipeline_corrections_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_corrections_status ON public.pipeline_corrections USING btree (status);


--
-- Name: ix_pipeline_jobs_correction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_correction_id ON public.pipeline_jobs USING btree (correction_id);


--
-- Name: ix_pipeline_jobs_current_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_current_stage ON public.pipeline_jobs USING btree (current_stage);


--
-- Name: ix_pipeline_jobs_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_raw_item_id ON public.pipeline_jobs USING btree (raw_item_id);


--
-- Name: ix_pipeline_jobs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_pipeline_jobs_status ON public.pipeline_jobs USING btree (status);


--
-- Name: ix_processing_checkpoints_correction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_correction_id ON public.processing_checkpoints USING btree (correction_id);


--
-- Name: ix_processing_checkpoints_event_aggregation_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_event_aggregation_run_id ON public.processing_checkpoints USING btree (event_aggregation_run_id);


--
-- Name: ix_processing_checkpoints_normalized_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_normalized_item_id ON public.processing_checkpoints USING btree (normalized_item_id);


--
-- Name: ix_processing_checkpoints_processing_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_processing_run_id ON public.processing_checkpoints USING btree (processing_run_id);


--
-- Name: ix_processing_checkpoints_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_raw_item_id ON public.processing_checkpoints USING btree (raw_item_id);


--
-- Name: ix_processing_checkpoints_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_checkpoints_stage ON public.processing_checkpoints USING btree (stage);


--
-- Name: ix_processing_runs_correction_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_correction_id ON public.processing_runs USING btree (correction_id);


--
-- Name: ix_processing_runs_current_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_current_stage ON public.processing_runs USING btree (current_stage);


--
-- Name: ix_processing_runs_execution_mode; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_execution_mode ON public.processing_runs USING btree (execution_mode);


--
-- Name: ix_processing_runs_outcome; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_outcome ON public.processing_runs USING btree (outcome);


--
-- Name: ix_processing_runs_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_raw_item_id ON public.processing_runs USING btree (raw_item_id);


--
-- Name: ix_processing_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_status ON public.processing_runs USING btree (status);


--
-- Name: ix_processing_runs_supersedes_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_supersedes_run_id ON public.processing_runs USING btree (supersedes_run_id);


--
-- Name: ix_processing_runs_workflow_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_processing_runs_workflow_type ON public.processing_runs USING btree (workflow_type);


--
-- Name: ix_raw_item_source_payloads_provider; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_item_source_payloads_provider ON public.raw_item_source_payloads USING btree (provider);


--
-- Name: ix_raw_item_source_payloads_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_item_source_payloads_raw_item_id ON public.raw_item_source_payloads USING btree (raw_item_id);


--
-- Name: ix_raw_items_content_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_items_content_hash ON public.raw_items USING btree (content_hash);


--
-- Name: ix_raw_items_external_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_items_external_id ON public.raw_items USING btree (external_id);


--
-- Name: ix_raw_items_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_items_source_id ON public.raw_items USING btree (source_id);


--
-- Name: ix_raw_items_supersedes_raw_item_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_raw_items_supersedes_raw_item_id ON public.raw_items USING btree (supersedes_raw_item_id);


--
-- Name: ix_review_tasks_decision_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_tasks_decision_source ON public.review_tasks USING btree (decision_source);


--
-- Name: ix_review_tasks_processing_run_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_tasks_processing_run_id ON public.review_tasks USING btree (processing_run_id);


--
-- Name: ix_review_tasks_stage; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_tasks_stage ON public.review_tasks USING btree (stage);


--
-- Name: ix_review_tasks_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_review_tasks_status ON public.review_tasks USING btree (status);


--
-- Name: ix_source_collection_schedules_enabled; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_enabled ON public.source_collection_schedules USING btree (enabled);


--
-- Name: ix_source_collection_schedules_last_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_last_status ON public.source_collection_schedules USING btree (last_status);


--
-- Name: ix_source_collection_schedules_lease_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_lease_expires_at ON public.source_collection_schedules USING btree (lease_expires_at);


--
-- Name: ix_source_collection_schedules_lease_token; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_lease_token ON public.source_collection_schedules USING btree (lease_token);


--
-- Name: ix_source_collection_schedules_next_run_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_next_run_at ON public.source_collection_schedules USING btree (next_run_at);


--
-- Name: ix_source_collection_schedules_run_requested_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_run_requested_at ON public.source_collection_schedules USING btree (run_requested_at);


--
-- Name: ix_source_collection_schedules_source_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_source_collection_schedules_source_id ON public.source_collection_schedules USING btree (source_id);


--
-- Name: ix_sources_connector_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sources_connector_type ON public.sources USING btree (connector_type);


--
-- Name: ix_sources_name; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_sources_name ON public.sources USING btree (name);


--
-- Name: uq_event_aggregation_runs_active_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_event_aggregation_runs_active_item ON public.event_aggregation_runs USING btree (normalized_item_id) WHERE ((status)::text = ANY ((ARRAY['running'::character varying, 'awaiting_review'::character varying])::text[]));


--
-- Name: uq_event_messages_active_normalized_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_event_messages_active_normalized_item ON public.event_messages USING btree (normalized_item_id) WHERE ((membership_status)::text = 'active'::text);


--
-- Name: uq_event_review_tasks_pending_run; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_event_review_tasks_pending_run ON public.event_review_tasks USING btree (event_aggregation_run_id) WHERE ((status)::text = 'pending'::text);


--
-- Name: uq_pipeline_jobs_active_raw_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_pipeline_jobs_active_raw_item ON public.pipeline_jobs USING btree (raw_item_id) WHERE ((status)::text = ANY ((ARRAY['queued'::character varying, 'running'::character varying])::text[]));


--
-- Name: uq_processing_runs_active_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_processing_runs_active_item ON public.processing_runs USING btree (raw_item_id) WHERE (((workflow_type)::text = 'item'::text) AND ((status)::text = ANY ((ARRAY['running'::character varying, 'awaiting_review'::character varying])::text[])));


--
-- Name: uq_raw_items_source_external_hash; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_raw_items_source_external_hash ON public.raw_items USING btree (source_id, external_id, content_hash) WHERE (external_id IS NOT NULL);


--
-- Name: uq_raw_items_source_hash_without_external; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_raw_items_source_hash_without_external ON public.raw_items USING btree (source_id, content_hash) WHERE (external_id IS NULL);


--
-- Name: uq_review_tasks_pending_run; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_review_tasks_pending_run ON public.review_tasks USING btree (processing_run_id) WHERE ((status)::text = 'pending'::text);


--
-- Name: uq_sources_connector_external_key; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX uq_sources_connector_external_key ON public.sources USING btree (connector_type, external_key) WHERE (external_key IS NOT NULL);


--
-- Name: connector_runs connector_runs_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.connector_runs
    ADD CONSTRAINT connector_runs_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: event_aggregation_runs event_aggregation_runs_correction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_aggregation_runs
    ADD CONSTRAINT event_aggregation_runs_correction_id_fkey FOREIGN KEY (correction_id) REFERENCES public.pipeline_corrections(id) ON DELETE SET NULL;


--
-- Name: event_aggregation_runs event_aggregation_runs_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_aggregation_runs
    ADD CONSTRAINT event_aggregation_runs_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE RESTRICT;


--
-- Name: event_aggregation_runs event_aggregation_runs_supersedes_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_aggregation_runs
    ADD CONSTRAINT event_aggregation_runs_supersedes_run_id_fkey FOREIGN KEY (supersedes_run_id) REFERENCES public.event_aggregation_runs(id) ON DELETE SET NULL;


--
-- Name: event_messages event_messages_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_messages
    ADD CONSTRAINT event_messages_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: event_messages event_messages_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_messages
    ADD CONSTRAINT event_messages_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE RESTRICT;


--
-- Name: event_messages event_messages_source_correction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_messages
    ADD CONSTRAINT event_messages_source_correction_id_fkey FOREIGN KEY (source_correction_id) REFERENCES public.pipeline_corrections(id) ON DELETE SET NULL;


--
-- Name: event_review_tasks event_review_tasks_event_aggregation_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_review_tasks
    ADD CONSTRAINT event_review_tasks_event_aggregation_run_id_fkey FOREIGN KEY (event_aggregation_run_id) REFERENCES public.event_aggregation_runs(id) ON DELETE CASCADE;


--
-- Name: event_revisions event_revisions_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.event_revisions
    ADD CONSTRAINT event_revisions_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE CASCADE;


--
-- Name: pipeline_corrections fk_pipeline_corrections_checkpoint_id; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT fk_pipeline_corrections_checkpoint_id FOREIGN KEY (checkpoint_id) REFERENCES public.processing_checkpoints(id) ON DELETE SET NULL;


--
-- Name: glossary_terms glossary_terms_source_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.glossary_terms
    ADD CONSTRAINT glossary_terms_source_review_id_fkey FOREIGN KEY (source_review_id) REFERENCES public.review_tasks(id) ON DELETE SET NULL;


--
-- Name: knowledge_rules knowledge_rules_source_event_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules
    ADD CONSTRAINT knowledge_rules_source_event_review_id_fkey FOREIGN KEY (source_event_review_id) REFERENCES public.event_review_tasks(id) ON DELETE SET NULL;


--
-- Name: knowledge_rules knowledge_rules_source_review_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.knowledge_rules
    ADD CONSTRAINT knowledge_rules_source_review_id_fkey FOREIGN KEY (source_review_id) REFERENCES public.review_tasks(id) ON DELETE SET NULL;


--
-- Name: media_assets media_assets_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_assets
    ADD CONSTRAINT media_assets_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE CASCADE;


--
-- Name: media_extractions media_extractions_media_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.media_extractions
    ADD CONSTRAINT media_extractions_media_asset_id_fkey FOREIGN KEY (media_asset_id) REFERENCES public.media_assets(id) ON DELETE CASCADE;


--
-- Name: normalized_item_media_extractions normalized_item_media_extractions_media_extraction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_media_extractions
    ADD CONSTRAINT normalized_item_media_extractions_media_extraction_id_fkey FOREIGN KEY (media_extraction_id) REFERENCES public.media_extractions(id) ON DELETE RESTRICT;


--
-- Name: normalized_item_media_extractions normalized_item_media_extractions_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_media_extractions
    ADD CONSTRAINT normalized_item_media_extractions_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE CASCADE;


--
-- Name: normalized_item_revisions normalized_item_revisions_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_revisions
    ADD CONSTRAINT normalized_item_revisions_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE CASCADE;


--
-- Name: normalized_item_revisions normalized_item_revisions_processing_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_item_revisions
    ADD CONSTRAINT normalized_item_revisions_processing_run_id_fkey FOREIGN KEY (processing_run_id) REFERENCES public.processing_runs(id) ON DELETE SET NULL;


--
-- Name: normalized_items normalized_items_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.normalized_items
    ADD CONSTRAINT normalized_items_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE CASCADE;


--
-- Name: ocr_profiles ocr_profiles_source_test_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_profiles
    ADD CONSTRAINT ocr_profiles_source_test_run_id_fkey FOREIGN KEY (source_test_run_id) REFERENCES public.ocr_test_runs(id) ON DELETE SET NULL;


--
-- Name: ocr_test_runs ocr_test_runs_media_asset_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ocr_test_runs
    ADD CONSTRAINT ocr_test_runs_media_asset_id_fkey FOREIGN KEY (media_asset_id) REFERENCES public.media_assets(id) ON DELETE CASCADE;


--
-- Name: pipeline_corrections pipeline_corrections_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.events(id) ON DELETE SET NULL;


--
-- Name: pipeline_corrections pipeline_corrections_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE RESTRICT;


--
-- Name: pipeline_corrections pipeline_corrections_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE RESTRICT;


--
-- Name: pipeline_corrections pipeline_corrections_source_event_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_source_event_run_id_fkey FOREIGN KEY (source_event_run_id) REFERENCES public.event_aggregation_runs(id) ON DELETE SET NULL;


--
-- Name: pipeline_corrections pipeline_corrections_source_processing_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_corrections
    ADD CONSTRAINT pipeline_corrections_source_processing_run_id_fkey FOREIGN KEY (source_processing_run_id) REFERENCES public.processing_runs(id) ON DELETE SET NULL;


--
-- Name: pipeline_jobs pipeline_jobs_correction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_correction_id_fkey FOREIGN KEY (correction_id) REFERENCES public.pipeline_corrections(id) ON DELETE SET NULL;


--
-- Name: pipeline_jobs pipeline_jobs_event_aggregation_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_event_aggregation_run_id_fkey FOREIGN KEY (event_aggregation_run_id) REFERENCES public.event_aggregation_runs(id) ON DELETE SET NULL;


--
-- Name: pipeline_jobs pipeline_jobs_last_checkpoint_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_last_checkpoint_id_fkey FOREIGN KEY (last_checkpoint_id) REFERENCES public.processing_checkpoints(id) ON DELETE SET NULL;


--
-- Name: pipeline_jobs pipeline_jobs_processing_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_processing_run_id_fkey FOREIGN KEY (processing_run_id) REFERENCES public.processing_runs(id) ON DELETE SET NULL;


--
-- Name: pipeline_jobs pipeline_jobs_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pipeline_jobs
    ADD CONSTRAINT pipeline_jobs_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE RESTRICT;


--
-- Name: processing_checkpoints processing_checkpoints_correction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_correction_id_fkey FOREIGN KEY (correction_id) REFERENCES public.pipeline_corrections(id) ON DELETE SET NULL;


--
-- Name: processing_checkpoints processing_checkpoints_event_aggregation_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_event_aggregation_run_id_fkey FOREIGN KEY (event_aggregation_run_id) REFERENCES public.event_aggregation_runs(id) ON DELETE SET NULL;


--
-- Name: processing_checkpoints processing_checkpoints_normalized_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_normalized_item_id_fkey FOREIGN KEY (normalized_item_id) REFERENCES public.normalized_items(id) ON DELETE SET NULL;


--
-- Name: processing_checkpoints processing_checkpoints_processing_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_processing_run_id_fkey FOREIGN KEY (processing_run_id) REFERENCES public.processing_runs(id) ON DELETE SET NULL;


--
-- Name: processing_checkpoints processing_checkpoints_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_checkpoints
    ADD CONSTRAINT processing_checkpoints_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE RESTRICT;


--
-- Name: processing_runs processing_runs_correction_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_runs
    ADD CONSTRAINT processing_runs_correction_id_fkey FOREIGN KEY (correction_id) REFERENCES public.pipeline_corrections(id) ON DELETE SET NULL;


--
-- Name: processing_runs processing_runs_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_runs
    ADD CONSTRAINT processing_runs_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE CASCADE;


--
-- Name: processing_runs processing_runs_supersedes_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.processing_runs
    ADD CONSTRAINT processing_runs_supersedes_run_id_fkey FOREIGN KEY (supersedes_run_id) REFERENCES public.processing_runs(id) ON DELETE SET NULL;


--
-- Name: raw_item_source_payloads raw_item_source_payloads_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_item_source_payloads
    ADD CONSTRAINT raw_item_source_payloads_raw_item_id_fkey FOREIGN KEY (raw_item_id) REFERENCES public.raw_items(id) ON DELETE CASCADE;


--
-- Name: raw_items raw_items_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_items
    ADD CONSTRAINT raw_items_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id);


--
-- Name: raw_items raw_items_supersedes_raw_item_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.raw_items
    ADD CONSTRAINT raw_items_supersedes_raw_item_id_fkey FOREIGN KEY (supersedes_raw_item_id) REFERENCES public.raw_items(id) ON DELETE SET NULL;


--
-- Name: review_tasks review_tasks_processing_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.review_tasks
    ADD CONSTRAINT review_tasks_processing_run_id_fkey FOREIGN KEY (processing_run_id) REFERENCES public.processing_runs(id) ON DELETE CASCADE;


--
-- Name: source_collection_schedules source_collection_schedules_last_connector_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_collection_schedules
    ADD CONSTRAINT source_collection_schedules_last_connector_run_id_fkey FOREIGN KEY (last_connector_run_id) REFERENCES public.connector_runs(id) ON DELETE SET NULL;


--
-- Name: source_collection_schedules source_collection_schedules_source_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.source_collection_schedules
    ADD CONSTRAINT source_collection_schedules_source_id_fkey FOREIGN KEY (source_id) REFERENCES public.sources(id) ON DELETE CASCADE;


INSERT INTO public.schema_migrations(version) VALUES
('001_initial_schema'),
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

-- PostgreSQL database dump complete
