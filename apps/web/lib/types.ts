export type ContentBlock = {
  id: string;
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

export type PatchChange = {
  attribute: string;
  before: string | null;
  after: string | null;
  raw_text: string;
  confidence: number;
};

export type PatchPreviewData = {
  document_type: "patch_preview";
  patch: string | null;
  title: string;
  sections: Array<{
    section_type: string;
    label: string;
    entries: Array<{
      target: string;
      target_type: string;
      changes: PatchChange[];
    }>;
  }>;
  warnings: string[];
};

export type MediaExtraction = {
  media_asset_id: number;
  storage_path: string | null;
  task_type: string;
  status: string;
  confidence: number | null;
  structured_data: PatchPreviewData;
};

export type EventSourceItem = {
  normalized_item_id: number;
  raw_item_id: number;
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
  media_extractions: MediaExtraction[];
};

export type NewsEvent = {
  id: number;
  title: string;
  summary: string;
  category: string;
  entities: Array<{ name?: string; type?: string }>;
  importance_score: number;
  credibility: "official" | "corroborated" | "unverified" | "rumor" | string;
  occurred_at: string | null;
  created_at: string;
  items: EventSourceItem[];
};
