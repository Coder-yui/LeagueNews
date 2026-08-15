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
  entities: Array<{
    name?: string;
    display_name?: string;
    type?: string;
    canonical_name?: string | null;
    canonical_id?: string | null;
    role?: "core" | "context" | "affected";
  }>;
  products: string[];
  message_type: string;
  topics: string[];
  classification_version: string;
  content_form: string;
  facets: Record<string, unknown>;
  importance_score: number;
  importance_dimensions: Record<string, {
    score?: number;
    value?: string;
    evidence?: string;
  }>;
  importance_policy_version: string;
  priority_score: number;
  source_id: number;
  source_name: string;
  source_reliability_score: number;
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
  products: string[] | null;
  message_type: string | null;
  topics: string[] | null;
  summary: string | null;
  importance_score: number | null;
  current_pipeline_stage: string | null;
  current_pipeline_job_id: number | null;
  current_pipeline_job_status: string | null;
  current_pipeline_job_retry_pending: boolean;
  processing_runs: ProcessingRun[];
};

export type RawAdminPage = {
  items: RawAdminItem[];
  total: number;
  total_items: number;
  status_counts: Record<"all" | "failed" | "processing" | "completed", number>;
  source_options: { id: number; name: string }[];
  message_type_options: string[];
};

export type PublishedItemPage = {
  items: PublishedItem[];
  total: number;
  product_options: string[];
  message_type_options: string[];
  topic_options: string[];
};

export type PublishedDay = {
  date: string;
  count: number;
  latest_published_at: string;
};

export type PublishedDayList = {
  days: PublishedDay[];
  timezone: string;
};

export type DailyReport = {
  id: number;
  report_date: string;
  status: string;
  sections: {
    lolpc: PublishedItem[];
    esports: PublishedItem[];
    tft: PublishedItem[];
    other: PublishedItem[];
  };
  created_at: string;
  updated_at: string;
};

export type DailyReportSummary = {
  id: number;
  report_date: string;
  status: "published" | "withdrawn";
  item_count: number;
  section_counts: Record<"lolpc" | "esports" | "tft" | "other", number>;
  created_at: string;
  updated_at: string;
};

export type EventSource = {
  message_id: number;
  source_id: number;
  source_name: string;
  source_url: string | null;
  published_at: string | null;
};

export type EventCard = {
  id: number;
  title: string;
  current_summary: string;
  products: string[];
  event_family: string;
  category: "esports" | "lol_pc" | "tft" | "other_products" | "ecosystem";
  lifecycle_status: string;
  importance_score: number;
  importance_level: string;
  credibility_score: number;
  credibility_level: string;
  heat_score: number;
  heat_level: string;
  message_count: number;
  source_count: number;
  message_count_total: number;
  message_count_24h: number;
  unique_sources_24h: number;
  last_material_update_at: string | null;
  primary_source: EventSource | null;
  best_media_url: string | null;
};

export type EventTimelineNode = {
  mention_id: number;
  message_id: number;
  message_revision: number;
  occurred_at: string;
  relation: string;
  title: string;
  note: string;
  structured_fact_changes: Record<string, unknown>;
  source_id: number;
  source_name: string;
};

export type EventEvidence = {
  mention_id: number;
  message_id: number;
  message_revision: number;
  relation: string;
  source_role: string;
  materiality: string;
  independence_group: string | null;
  evidence_excerpt: string;
  source_id: number;
  source_name: string;
  source_url: string | null;
  published_at: string | null;
  content_form: string;
};

export type EventRelatedMessage = {
  message_id: number;
  title: string;
  summary: string;
  source_id: number;
  source_name: string;
  source_url: string | null;
  published_at: string | null;
  content_form: string;
};

export type EventDetail = EventCard & {
  latest_development: string;
  key_facts: Array<Record<string, unknown>>;
  canonical_anchors: Record<string, unknown>;
  importance_breakdown: Record<string, unknown>;
  credibility_breakdown: Record<string, unknown>;
  heat_breakdown: Record<string, unknown>;
  timeline: EventTimelineNode[];
  evidence: EventEvidence[];
  related_messages: EventRelatedMessage[];
  references: Record<string, number | null>;
};

export type EventPage = {
  items: EventCard[];
  total: number;
  product_options: string[];
  event_family_options: string[];
  lifecycle_options: string[];
  credibility_options: string[];
  category_options: Array<EventCard["category"]>;
};

export type PipelineJob = {
  id: number;
  raw_item_id: number;
  correction_id: number | null;
  status: string;
  current_stage: string;
  processing_run_id: number | null;
  attempts: number;
  next_attempt_at: string | null;
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

export type OCRTableRecord = {
  target: string;
  raw_changes: string[];
  bbox: number[];
  ocr_confidence: number;
};

export type OCRTableSection = {
  section_type: string;
  label: string;
  records: OCRTableRecord[];
};

export type OCRTableData = {
  preview_kind: "preview" | "full_preview";
  divider_x: number | null;
  structure_confidence: number;
  sections: OCRTableSection[];
  warnings: string[];
  boundaries: number[];
};

export type OCRReviewExtraction = {
  id: number;
  media_asset_id: number;
  block_index: number;
  source_url: string | null;
  storage_path: string | null;
  confidence: number | null;
  raw_ocr_text: string;
  table_data: OCRTableData;
};

export type OCRWorkflowReview = {
  review_id: number;
  processing_run_id: number;
  raw_item_id: number;
  raw_title: string | null;
  canonical_url: string | null;
  status: string;
  corrections: Array<Record<string, unknown>>;
  extractions: OCRReviewExtraction[];
  created_at: string;
};

export type ReviewQueueItem = {
  raw_item_id: number;
  raw_title: string | null;
  canonical_url: string | null;
  source_name: string;
  processing_run_id: number | null;
  normalized_item_id: number | null;
  current_stage: string;
  completed_stages: string[];
  review_kind: "message" | "ocr";
  message_review: ReviewTask | null;
  ocr_review: OCRWorkflowReview | null;
  created_at: string;
};

export type Source = {
  id: number;
  name: string;
  connector_type: string;
  external_key: string | null;
  is_active: boolean;
  is_official: boolean;
  reliability_score: number;
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
