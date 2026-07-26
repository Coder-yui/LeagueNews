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
  entities: Array<{ name?: string; type?: string; canonical_name?: string | null }>;
  importance_score: number;
  credibility: "official" | "corroborated" | "unverified" | "rumor" | string;
  credibility_score: number;
  credibility_evidence: string[];
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
  created_at: string;
};
