export type ContentBlock = {
  id?: string;
  type: "heading" | "paragraph" | "list" | "quote" | "image" | "embed";
  text?: string;
  level?: number;
  items?: string[];
  ordered?: boolean;
  embed_kind?: "video" | "poll" | "quoted_post" | "external_link" | "iframe" | "audio" | "other";
  source_url?: string;
  storage_path?: string;
  alt_text?: string;
  caption?: string;
  mime_type?: string;
};

export type PatchEntry = {
  target: string;
  target_type: string;
  changes: string[];
};

export type PatchSection = {
  section_type?: string;
  label: string;
  entries: PatchEntry[];
};

export type PatchPreviewData = {
  document_type?: "patch_preview";
  preview_kind?: "preview" | "full_preview";
  patch?: string | null;
  title?: string;
  sections?: PatchSection[];
  warnings?: string[];
};

export type PublishedMediaExtraction = {
  extraction_id: number;
  media_asset_id: number;
  block_index: number;
  storage_path: string | null;
  source_url: string | null;
  mime_type: string | null;
  confidence: number | null;
  original_data: PatchPreviewData;
  translated_data: PatchPreviewData;
};

export type PublishedItem = {
  id: number;
  raw_item_id: number;
  title: string;
  summary: string;
  category: string;
  content_type?: string | null;
  entities: Array<{
    name?: string;
    display_name?: string;
    type?: string;
    canonical_name?: string | null;
    canonical_id?: string | null;
  }>;
  primary_topic: string;
  secondary_topics: string[];
  facets: Record<string, unknown>;
  ontology_version: string;
  importance_score: number;
  importance_dimensions: Record<string, { score?: number; evidence?: string }>;
  importance_policy_version: string;
  credibility: "official" | "corroborated" | "unverified" | "rumor" | string;
  credibility_score: number;
  credibility_evidence: string[];
  credibility_components: Record<string, unknown>;
  credibility_policy_version: string;
  source_id: number;
  source_name: string;
  source_base_url: string | null;
  source_url: string | null;
  author: string | null;
  published_at: string | null;
  original_title: string | null;
  original_content_blocks: ContentBlock[];
  source_language: string | null;
  translated_title: string | null;
  translated_content_blocks: ContentBlock[];
  translation_status: string;
  media_extractions: PublishedMediaExtraction[];
  fact_claims?: Array<{
    id: number;
    subject: Record<string, unknown>;
    predicate: string;
    object_value: Record<string, unknown>;
    attribution: Record<string, unknown>;
    stance: string;
    confidence: number;
  }>;
  event_memberships?: Array<{
    event_id: number;
    event_title: string;
    event_type: string;
    membership_role: string;
    evidence_stance: string;
  }>;
  created_at: string;
};

export type ProcessingRun = {
  id: number;
  raw_item_id?: number;
  status: string;
  outcome: string | null;
  current_stage: string;
  context: Record<string, unknown>;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
};

export type RawAdminItem = {
  id: number;
  source_id: number;
  source_name: string;
  source_connector_type: string;
  external_id: string | null;
  native_title: string | null;
  display_title: string | null;
  content_kind: string;
  author_name: string | null;
  language: string | null;
  canonical_url: string | null;
  content_blocks: ContentBlock[];
  processing_status: string;
  published_at: string | null;
  ingested_at: string;
  normalized_item_id: number | null;
  content_type: string | null;
  summary: string | null;
  credibility_score: number | null;
  importance_score: number | null;
  current_pipeline_stage: string | null;
  current_pipeline_job_id: number | null;
  current_pipeline_job_status: string | null;
  processing_runs: ProcessingRun[];
};

export type RawAdminPage = {
  items: RawAdminItem[];
  total: number;
  total_items: number;
  status_counts: Record<"all" | "failed" | "processing" | "completed", number>;
  source_options: { id: number; name: string }[];
  content_type_options: string[];
};

export type PublishedItemPage = {
  items: PublishedItem[];
  total: number;
  topic_options: string[];
  content_type_options: string[];
};

export type PipelineJob = {
  id: number;
  raw_item_id: number;
  correction_id: number | null;
  status: string;
  current_stage: string;
  processing_run_id: number | null;
  attempts: number;
  error_message: string | null;
  last_checkpoint_id: number | null;
  created_at: string;
  started_at: string | null;
  updated_at: string;
  completed_at: string | null;
};

export type PipelineCorrection = {
  id: number;
  raw_item_id: number;
  normalized_item_id: number | null;
  event_id: number | null;
  restart_from_stage: string;
  resume_mode: string;
  reason: string;
  status: string;
  error_message: string | null;
  requested_at: string;
  completed_at: string | null;
};

export type ReviewTask = {
  id: number;
  processing_run_id: number;
  stage: string;
  status: string;
  proposal: Record<string, unknown>;
  feedback: Record<string, unknown>;
  created_at: string;
};

export type EventReviewTask = {
  id: number;
  event_aggregation_run_id: number;
  status: string;
  proposal: Record<string, unknown>;
  feedback: Record<string, unknown>;
  created_at: string;
};

export type Source = {
  id: number;
  name: string;
  connector_type: string;
  external_key: string | null;
  is_active: boolean;
  created_at: string;
};

export type CollectionSchedule = {
  id: number;
  source_id: number;
  source_name: string;
  connector_type: string;
  enabled: boolean;
  interval_minutes: number;
  retry_delay_minutes: number;
  fetch_limit: number;
  overlap_minutes: number;
  options: Record<string, unknown>;
  collection_cursor: Record<string, unknown>;
  next_run_at: string | null;
  run_requested_at: string | null;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_success_at: string | null;
  last_status: string;
  last_error: string | null;
  consecutive_failures: number;
  updated_at: string;
};

export type ConnectorRun = {
  id: number;
  source_id: number;
  connector_type: string;
  status: string;
  discovered_count: number;
  created_count: number;
  revised_count: number;
  skipped_count: number;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
};

export type ConnectorRunPage = {
  items: ConnectorRun[];
  total: number;
};

export type EventSummary = {
  id: number;
  event_key: string | null;
  aggregation_key?: string | null;
  title: string;
  summary: string;
  category: string;
  status: string;
  event_type: string;
  lifecycle_status: string;
  credibility_status: string;
  credibility_score: number;
  importance_score: number;
  importance_evidence: string[];
  latest_development: string;
  independent_source_count: number;
  official_source_count: number;
  first_published_at: string | null;
  last_published_at: string | null;
  current_revision: number;
  message_count: number;
  created_at: string;
  updated_at: string;
};

export type EventPage = {
  items: EventSummary[];
  total: number;
};

export type EventMessage = {
  normalized_item_id: number;
  relation_type: string;
  membership_role?: string | null;
  evidence_stance: string;
  is_official_confirmation: boolean;
  is_significant_update: boolean;
  source_published_at: string | null;
  added_at: string;
  title: string;
  summary: string;
  source_name: string;
  source_url: string | null;
  credibility_score?: number | null;
  credibility_components?: Record<string, unknown>;
};

export type EventRevision = {
  id: number;
  revision: number;
  title: string;
  summary: string;
  change_note: string;
  evidence_snapshot: Record<string, unknown>;
  created_at: string;
};

export type EventDetail = EventSummary & {
  messages: EventMessage[];
  revisions: EventRevision[];
};

export type Digest = {
  id: number;
  digest_type: "daily" | "weekly";
  timezone: string;
  window_start: string;
  cutoff_at: string;
  language: string;
  title: string;
  body: string;
  current_revision: number;
  input_snapshot: Array<{
    event_id: number;
    event_revision: number;
    title: string;
    summary: string;
    importance_score: number;
  }>;
  generation_metadata: Record<string, unknown>;
  published_at: string;
  updated_at: string;
};
