"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  BookOpenCheck,
  Check,
  FileClock,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ScanText,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

type JsonObject = Record<string, unknown>;

type RawItem = {
  id: number;
  source_id: number;
  display_title: string | null;
  author_name: string | null;
  canonical_url: string | null;
  processing_status: string;
  published_at: string | null;
};

type ProcessingRun = {
  id: number;
  raw_item_id: number;
  workflow_type: string;
  status: string;
  current_stage: string;
  error_message: string | null;
};

type ReviewTask = {
  id: number;
  processing_run_id: number;
  stage: string;
  status: string;
  proposal: JsonObject;
};

type KnowledgeRule = {
  id: number;
  knowledge_type: string;
  scope: string;
  rule_text: string;
  version: number;
  is_active: boolean;
};

type GlossaryTerm = {
  id: number;
  source_term: string;
  preferred_translation: string;
  forbidden_translations: string[];
  scope: string;
  version: number;
  is_active: boolean;
};

type OCRParameters = {
  scale: number;
  grayscale: boolean;
  contrast: number;
  sharpness: number;
  text_score: number | null;
  box_thresh: number | null;
  unclip_ratio: number | null;
  use_cls: boolean;
  divider_x_ratio: number | null;
  line_brightness: number;
  line_coverage: number;
};

type OCRAsset = {
  media_asset_id: number;
  raw_item_id: number;
  raw_title: string | null;
  published_at: string | null;
  block_index: number;
  storage_path: string;
  source_url: string | null;
  width: number | null;
  height: number | null;
};

type OCRLine = {
  index: number;
  text: string;
  confidence: number | null;
  box: number[][] | null;
};

type PatchTableRecord = {
  target: string;
  raw_changes: string[];
  bbox: number[];
  ocr_confidence: number;
};

type PatchTableSection = {
  section_type: string;
  label: string;
  records: PatchTableRecord[];
};

type PatchTableData = {
  preview_kind?: "preview" | "full_preview";
  divider_x?: number | null;
  structure_confidence?: number;
  warnings?: string[];
  sections?: PatchTableSection[];
  boundaries?: number[];
};

type OCRTestRun = {
  id: number;
  media_asset_id: number;
  profile_name: string;
  parameters: OCRParameters;
  status: string;
  raw_text: string;
  lines: OCRLine[];
  confidence: number;
  source_width: number;
  source_height: number;
  processed_width: number;
  processed_height: number;
  overlay_path: string | null;
  table_overlay_path: string | null;
  table_data: PatchTableData;
  structure_confidence: number | null;
  engine: string;
  created_at: string;
};

type MediaExtraction = {
  id: number;
  media_asset_id: number;
  provider: string;
  schema_version: string;
  ocr_lines: OCRLine[];
  structured_data: JsonObject;
  processing_config: {
    table_data?: PatchTableData;
    manual_correction?: JsonObject;
  };
  confidence: number | null;
};

type OCRCorrectionDraft = {
  extractionId: number;
  tableData: PatchTableData;
  dirty: boolean;
  invalid: boolean;
};

type GlossaryCorrectionDraft = {
  id: number;
  source_term: string;
  preferred_translation: string;
};

type OCRProfile = {
  id: number;
  name: string;
  parameters: OCRParameters;
  source_test_run_id: number | null;
  is_active: boolean;
};

type Tab = "items" | "reviews" | "ocr" | "knowledge";

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const stageLabels: Record<string, string> = {
  relevance: "相关性审核",
  image_ocr: "图片 OCR 审核",
  item_analysis: "分析与摘要审核",
  translation: "翻译与术语审核",
};

function latestRunsByRawItem(runs: ProcessingRun[]): ProcessingRun[] {
  const latest = new Map<number, ProcessingRun>();
  for (const run of runs) {
    const current = latest.get(run.raw_item_id);
    if (!current || run.id > current.id) latest.set(run.raw_item_id, run);
  }
  return Array.from(latest.values());
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("items");
  const [rawItems, setRawItems] = useState<RawItem[]>([]);
  const [runs, setRuns] = useState<ProcessingRun[]>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [rules, setRules] = useState<KnowledgeRule[]>([]);
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [ocrAssets, setOcrAssets] = useState<OCRAsset[]>([]);
  const [ocrRuns, setOcrRuns] = useState<OCRTestRun[]>([]);
  const [ocrProfiles, setOcrProfiles] = useState<OCRProfile[]>([]);
  const [mediaExtractions, setMediaExtractions] = useState<MediaExtraction[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [
        raw,
        runRows,
        reviewRows,
        ruleRows,
        termRows,
        ocrAssetRows,
        ocrRunRows,
        ocrProfileRows,
        extractionRows,
      ] =
        await Promise.all([
          api<RawItem[]>("/raw-items"),
          api<ProcessingRun[]>("/workflows/runs"),
          api<ReviewTask[]>("/workflows/reviews?status=pending"),
          api<KnowledgeRule[]>("/knowledge/rules"),
          api<GlossaryTerm[]>("/knowledge/glossary"),
          api<OCRAsset[]>("/ocr-lab/assets"),
          api<OCRTestRun[]>("/ocr-lab/runs"),
          api<OCRProfile[]>("/ocr-lab/profiles"),
          api<MediaExtraction[]>("/media-assets/extractions"),
        ]);
      setRawItems(raw);
      setRuns(runRows);
      setReviews(reviewRows);
      setRules(ruleRows);
      setTerms(termRows);
      setOcrAssets(ocrAssetRows);
      setOcrRuns(ocrRunRows);
      setOcrProfiles(ocrProfileRows);
      setMediaExtractions(extractionRows);
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key);
    setMessage(null);
    try {
      await action();
      setMessage(success);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  const counts = useMemo(
    () => ({
      pending: rawItems.filter((item) => item.processing_status === "pending").length,
      reviews: reviews.length,
      retry: latestRunsByRawItem(runs)
        .filter((run) => ["failed", "rejected"].includes(run.status)).length,
      knowledge: rules.filter((rule) => rule.is_active).length + terms.filter((term) => term.is_active).length,
    }),
    [rawItems, reviews, runs, rules, terms],
  );

  return (
    <>
      <section className="admin-stats">
        <div><span>等待开始</span><strong>{counts.pending}</strong></div>
        <div><span>待人工审核</span><strong>{counts.reviews}</strong></div>
        <div><span>等待重试</span><strong>{counts.retry}</strong></div>
        <div><span>生效知识</span><strong>{counts.knowledge}</strong></div>
      </section>

      <div className="admin-tabs">
        {([
          ["items", "单条处理", FileClock],
          ["reviews", "审核中心", Check],
          ["ocr", "OCR 测试台", ScanText],
          ["knowledge", "知识与术语", BookOpenCheck],
        ] as const).map(([value, label, Icon]) => (
          <button
            className={tab === value ? "active" : ""}
            key={value}
            type="button"
            onClick={() => setTab(value)}
          >
            <Icon size={15} /> {label}
          </button>
        ))}
        <button className="refresh-button" type="button" onClick={() => void refresh()}>
          <RefreshCw size={14} /> 刷新
        </button>
      </div>

      {message && <div className="admin-message">{message}</div>}

      {tab === "items" && (
        <ItemsPanel
          rawItems={rawItems}
          runs={runs}
          busy={busy}
          act={act}
        />
      )}
      {tab === "reviews" && (
        <ReviewsPanel
          reviews={reviews}
          mediaExtractions={mediaExtractions}
          ocrAssets={ocrAssets}
          busy={busy}
          act={act}
        />
      )}
      {tab === "ocr" && (
        <OCRLabPanel
          assets={ocrAssets}
          runs={ocrRuns}
          profiles={ocrProfiles}
          busy={busy}
          act={act}
        />
      )}
      {tab === "knowledge" && (
        <KnowledgePanel rules={rules} terms={terms} busy={busy} act={act} />
      )}
    </>
  );
}

function ItemsPanel({
  rawItems,
  runs,
  busy,
  act,
}: {
  rawItems: RawItem[];
  runs: ProcessingRun[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const retryRuns = latestRunsByRawItem(runs)
    .filter((run) => ["failed", "rejected"].includes(run.status));
  return (
    <section className="admin-panel">
      {retryRuns.length > 0 && (
        <div className="retry-strip">
          <strong>需要修订或重试</strong>
          {retryRuns.map((run) => (
            <button
              key={run.id}
              type="button"
              disabled={busy === `retry-${run.id}`}
              onClick={() =>
                void act(
                  `retry-${run.id}`,
                  () => api(`/workflows/runs/${run.id}/retry`, { method: "POST" }),
                  `运行 #${run.id} 已重新生成审核草稿`,
                )
              }
            >
              <RotateCcw size={13} /> #{run.id} · {run.current_stage}
            </button>
          ))}
        </div>
      )}
      <div className="admin-list">
        {rawItems.map((item) => {
          const canStart = item.processing_status === "pending";
          return (
            <article className="admin-item" key={item.id}>
              <div className="admin-item-meta">
                <span>RAW #{item.id}</span>
                <span>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "无发布日期"}</span>
                <b>{item.processing_status}</b>
              </div>
              <h3>{item.display_title ?? "无标题信息"}</h3>
              <p>{item.author_name ?? `Source #${item.source_id}`}</p>
              <div className="admin-actions">
                {item.canonical_url && <a href={item.canonical_url} target="_blank" rel="noreferrer">查看原文</a>}
                {canStart && (
                  <button
                    type="button"
                    disabled={busy === `raw-${item.id}`}
                    onClick={() =>
                      void act(
                        `raw-${item.id}`,
                        () => api(`/raw-items/${item.id}/process`, { method: "POST" }),
                        `Raw #${item.id} 已进入相关性审核`,
                      )
                    }
                  >
                    {busy === `raw-${item.id}` ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}
                    开始 AI 处理
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ReviewsPanel({
  reviews,
  mediaExtractions,
  ocrAssets,
  busy,
  act,
}: {
  reviews: ReviewTask[];
  mediaExtractions: MediaExtraction[];
  ocrAssets: OCRAsset[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  if (!reviews.length) return <section className="admin-empty">目前没有待审核草稿。</section>;
  return (
    <section className="admin-list">
      {reviews.map((review) => (
        <ReviewCard
          review={review}
          mediaExtractions={mediaExtractions}
          ocrAssets={ocrAssets}
          busy={busy}
          act={act}
          key={review.id}
        />
      ))}
    </section>
  );
}

function ReviewCard({
  review,
  mediaExtractions,
  ocrAssets,
  busy,
  act,
}: {
  review: ReviewTask;
  mediaExtractions: MediaExtraction[];
  ocrAssets: OCRAsset[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const approvedExtractionIds = Array.isArray(review.proposal.approved_media_extraction_ids)
    ? review.proposal.approved_media_extraction_ids.filter(
        (value): value is number => typeof value === "number",
      )
    : [];
  const reviewExtractions = mediaExtractions.filter((extraction) =>
    approvedExtractionIds.includes(extraction.id),
  );
  const defaultFeedbackType =
    review.stage === "relevance"
      ? "relevance_correction"
      : review.stage === "image_ocr"
        ? "ocr_error"
        : review.stage === "translation"
          ? "translation_correction"
          : "analysis_correction";
  const [reason, setReason] = useState("");
  const [glossaryUpdates, setGlossaryUpdates] = useState<GlossaryCorrectionDraft[]>([
    { id: 1, source_term: "", preferred_translation: "" },
  ]);
  const [feedbackType, setFeedbackType] = useState(defaultFeedbackType);
  const [ocrDrafts, setOcrDrafts] = useState<Record<number, OCRCorrectionDraft>>({});
  const updateOCRDraft = useCallback((draft: OCRCorrectionDraft) => {
    setOcrDrafts((current) => ({ ...current, [draft.extractionId]: draft }));
  }, []);
  const learnsRule = [
    "relevance_correction",
    "analysis_correction",
  ].includes(feedbackType);
  const learnsTerm = ["translation_term", "translation_correction"].includes(
    feedbackType,
  );
  const trimmedReason = reason.trim();
  const completeGlossaryUpdates = glossaryUpdates.filter(
    (item) => item.source_term.trim() && item.preferred_translation.trim(),
  );
  const hasIncompleteGlossaryUpdate = glossaryUpdates.some(
    (item) =>
      Boolean(item.source_term.trim()) !== Boolean(item.preferred_translation.trim()),
  );
  const rejectPayload = {
    feedback_type: feedbackType,
    reason: trimmedReason || null,
    knowledge_rule: learnsRule ? reason : null,
    knowledge_scope: "global",
    corrected_values: {},
    glossary_updates:
      learnsTerm
        ? completeGlossaryUpdates.map((item) => ({
            source_term: item.source_term.trim(),
            preferred_translation: item.preferred_translation.trim(),
            forbidden_translations: [],
          }))
        : [],
  };
  const rejectSuccess =
    feedbackType === "ocr_error"
      ? "草稿已退回；OCR 错误已记录，但不会写入知识或术语"
      : learnsTerm
        ? "草稿已退回，翻译规则和术语修正已分别沉淀"
        : "草稿已退回，反馈已成为可编辑的长期规则";
  const changedOCRDrafts = Object.values(ocrDrafts).filter((draft) => draft.dirty);
  const invalidOCRDrafts = Object.values(ocrDrafts).filter((draft) => draft.invalid);
  const changedOCRDraft = changedOCRDrafts.length === 1 ? changedOCRDrafts[0] : null;
  const ocrActionKey = changedOCRDraft
    ? `correct-ocr-${review.id}-${changedOCRDraft.extractionId}`
    : `correct-ocr-${review.id}`;
  return (
    <article className="review-card">
      <div className="review-heading">
        <div>
          <span>REVIEW #{review.id} · RUN #{review.processing_run_id}</span>
          <h3>{stageLabels[review.stage] ?? review.stage}</h3>
        </div>
        <b>等待确认</b>
      </div>
      {review.stage === "item_analysis" ? (
        <AnalysisReview proposal={review.proposal} />
      ) : review.stage === "translation" ? (
        <TranslationReview proposal={review.proposal} />
      ) : review.stage === "image_ocr" ? (
        <>
          {reviewExtractions.map((extraction) => (
            <OCRCorrectionEditor
              key={extraction.id}
              extraction={extraction}
              asset={ocrAssets.find(
                (asset) => asset.media_asset_id === extraction.media_asset_id,
              )}
              onDraftChange={updateOCRDraft}
            />
          ))}
        </>
      ) : (
        <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
      )}
      {review.stage !== "image_ocr" && (
        <div className="review-form">
          {["item_analysis", "translation"].includes(review.stage) ? (
          <div className="review-feedback-context">
            <strong>
              {review.stage === "translation"
                  ? "翻译术语修正"
                  : "分析与摘要修正"}
            </strong>
            <span>
              {review.stage === "translation"
                  ? "可只填写术语修正；如需说明句子译法，再填写退回理由。两者至少填写一种。"
                  : "说明分析、分类或摘要的问题，退回后会沉淀为分析规则。"}
            </span>
          </div>
        ) : (
          <label>
            反馈类型
            <select value={feedbackType} onChange={(event) => setFeedbackType(event.target.value)}>
            {review.stage === "relevance" && (
              <option value="relevance_correction">相关性判断错误（沉淀规则）</option>
            )}
            </select>
          </label>
          )}
          <label>
            退回理由
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={
                review.stage === "translation"
                  ? "可选：说明句子应该如何翻译。"
                  : "说明错误和正确处理方式。"
              }
            />
          </label>
          {learnsTerm && (
            <div className="glossary-corrections">
              {glossaryUpdates.map((item, index) => (
                <div className="glossary-correction" key={item.id}>
                  <label>
                    错误原词
                    <input
                      value={item.source_term}
                      onChange={(event) =>
                        setGlossaryUpdates((current) =>
                          current.map((entry) =>
                            entry.id === item.id
                              ? { ...entry, source_term: event.target.value }
                              : entry,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    标准译名
                    <input
                      value={item.preferred_translation}
                      onChange={(event) =>
                        setGlossaryUpdates((current) =>
                          current.map((entry) =>
                            entry.id === item.id
                              ? { ...entry, preferred_translation: event.target.value }
                              : entry,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    className="remove-glossary-row"
                    type="button"
                    disabled={glossaryUpdates.length === 1}
                    aria-label={`删除第 ${index + 1} 项术语修正`}
                    onClick={() =>
                      setGlossaryUpdates((current) =>
                        current.filter((entry) => entry.id !== item.id),
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <button
                className="add-glossary-row"
                type="button"
                onClick={() =>
                  setGlossaryUpdates((current) => [
                    ...current,
                    {
                      id: Math.max(...current.map((item) => item.id)) + 1,
                      source_term: "",
                      preferred_translation: "",
                    },
                  ])
                }
              >
                <Plus size={14} /> 添加术语修正
              </button>
            </div>
          )}
        </div>
      )}
      {["item_analysis", "translation"].includes(review.stage) && (
        <details className="review-json-details">
          <summary>查看完整审核草稿 JSON</summary>
          <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
        </details>
      )}
      <div className="admin-actions">
        <button
          className="approve"
          type="button"
          disabled={
            changedOCRDrafts.length > 0 ||
            invalidOCRDrafts.length > 0 ||
            busy === `approve-${review.id}`
          }
          onClick={() => {
            void act(
              `approve-${review.id}`,
              () => api(`/workflows/reviews/${review.id}/approve`, {
                method: "POST",
                body: JSON.stringify({ note: null }),
              }),
              "审核已批准，正式数据或下一审核阶段已生成",
            );
          }}
        >
          <Check size={14} />{" "}
          {review.stage === "item_analysis"
            ? "批准分析，进入翻译审核"
            : review.stage === "translation"
              ? "批准翻译，完成处理"
            : review.stage === "image_ocr"
              ? "批准 OCR"
              : "批准"}
        </button>
        {review.stage === "image_ocr" ? (
          <button
            className="ocr-correction-action"
            type="button"
            disabled={
              !changedOCRDraft ||
              changedOCRDraft.invalid ||
              changedOCRDrafts.length !== 1 ||
              busy === ocrActionKey
            }
            onClick={() => {
              if (!changedOCRDraft) return;
              void act(
                ocrActionKey,
                () =>
                  api(`/workflows/reviews/${review.id}/correct-ocr`, {
                    method: "POST",
                    body: JSON.stringify({
                      extraction_id: changedOCRDraft.extractionId,
                      table_data: changedOCRDraft.tableData,
                    }),
                  }),
                "OCR 修改已保存，草稿已退回并重新处理",
              );
            }}
          >
            <RotateCcw size={14} /> 保存修改并退回重新处理
          </button>
        ) : (
          <button
            className="reject"
            type="button"
            disabled={
              (!trimmedReason &&
                (!learnsTerm || completeGlossaryUpdates.length === 0)) ||
              (learnsTerm && hasIncompleteGlossaryUpdate) ||
              busy === `reject-${review.id}`
            }
            onClick={() =>
              void act(
                `reject-${review.id}`,
                () => api(`/workflows/reviews/${review.id}/reject`, { method: "POST", body: JSON.stringify(rejectPayload) }),
                rejectSuccess,
              )
            }
          >
            <X size={14} /> 退回并学习
          </button>
        )}
      </div>
    </article>
  );
}

function AnalysisReview({ proposal }: { proposal: JsonObject }) {
  const entities = Array.isArray(proposal.entities) ? proposal.entities : [];
  return (
    <section className="review-content-panel">
      <div className="review-field review-field-wide">
        <span>标准化标题</span>
        <strong>{textValue(proposal.normalized_title)}</strong>
      </div>
      <div className="review-field review-field-wide">
        <span>摘要</span>
        <p>{textValue(proposal.summary)}</p>
      </div>
      <div className="review-field">
        <span>分类</span>
        <strong>{textValue(proposal.category)}</strong>
      </div>
      <div className="review-field">
        <span>重要性</span>
        <strong>{scoreValue(proposal.importance_score)}</strong>
      </div>
      <div className="review-field">
        <span>可信度</span>
        <strong>
          {textValue(proposal.credibility)} · {scoreValue(proposal.credibility_score)}
        </strong>
      </div>
      <div className="review-field review-field-wide">
        <span>可信度依据</span>
        <p>
          {Array.isArray(proposal.credibility_evidence)
            ? proposal.credibility_evidence.map(textValue).join("；")
            : "—"}
        </p>
      </div>
      <div className="review-field review-field-wide">
        <span>实体</span>
        <div className="review-entity-list">
          {entities.length ? (
            entities.map((entity, index) => (
              <code key={index}>{entityLabel(entity)}</code>
            ))
          ) : (
            <em>未提取实体</em>
          )}
        </div>
      </div>
    </section>
  );
}

function TranslationReview({ proposal }: { proposal: JsonObject }) {
  const sourceStructures = Array.isArray(proposal.media_extractions)
    ? proposal.media_extractions
    : [];
  const translatedStructures = Array.isArray(
    proposal.translated_media_extractions,
  )
    ? proposal.translated_media_extractions
    : [];
  return (
    <section className="translation-review">
      <div className="translation-meta">
        <span>{textValue(proposal.source_language)} → {textValue(proposal.target_language)}</span>
        <b>{textValue(proposal.translation_status)}</b>
        <small>{textValue(proposal.translation_model)}</small>
      </div>
      <div className="translation-title">
        <span>中文标题</span>
        <strong>{textValue(proposal.translated_title)}</strong>
      </div>
      <div className="translation-columns">
        <article>
          <span>原文</span>
          <p>{textValue(proposal.normalized_text)}</p>
        </article>
        <article>
          <span>中文译文</span>
          <p>{textValue(proposal.translated_text)}</p>
        </article>
      </div>
      {sourceStructures.map((sourceStructure, index) => {
        const translated = translatedStructures[index];
        const translatedData =
          translated && typeof translated === "object"
            ? (translated as JsonObject).translated_data
            : null;
        return (
          <div className="translation-columns" key={`patch-translation-${index}`}>
            <article>
              <span>版本图片结构化原文 {index + 1}</span>
              <pre>{JSON.stringify(sourceStructure, null, 2)}</pre>
            </article>
            <article>
              <span>版本图片结构化中文 {index + 1}</span>
              <pre>{JSON.stringify(translatedData, null, 2)}</pre>
            </article>
          </div>
        );
      })}
    </section>
  );
}

function OCRCorrectionEditor({
  extraction,
  asset,
  onDraftChange,
}: {
  extraction: MediaExtraction;
  asset?: OCRAsset;
  onDraftChange: (draft: OCRCorrectionDraft) => void;
}) {
  const sourceTable = extraction.processing_config.table_data;
  const [tableData, setTableData] = useState<PatchTableData>(() =>
    sourceTable ? JSON.parse(JSON.stringify(sourceTable)) as PatchTableData : {},
  );
  const [activeSectionIndex, setActiveSectionIndex] = useState(0);
  const [activeRecordIndex, setActiveRecordIndex] = useState(0);
  const [imageExpanded, setImageExpanded] = useState(false);
  const changesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const sections = tableData.sections ?? [];
  const activeSection = sections[activeSectionIndex] ?? sections[0];
  const activeRecord = activeSection?.records[activeRecordIndex] ?? activeSection?.records[0];
  const sourceSectionLabel =
    sourceTable?.sections?.[activeSectionIndex]?.label ?? activeSection?.label;
  const activeSectionConfidence = findSectionOCRConfidence(
    extraction.ocr_lines,
    sourceSectionLabel,
  );

  useEffect(() => {
    const textarea = changesTextareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.max(textarea.scrollHeight, 180)}px`;
  }, [activeRecord?.raw_changes]);

  useEffect(() => {
    if (!imageExpanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImageExpanded(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imageExpanded]);

  const updateRecord = (
    sectionIndex: number,
    recordIndex: number,
    update: Partial<PatchTableRecord>,
  ) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? section.records.map((record, currentRecordIndex) =>
                currentRecordIndex === recordIndex ? { ...record, ...update } : record,
              )
            : section.records,
      })),
    }));
  };

  const removeRecord = (sectionIndex: number, recordIndex: number) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? section.records.filter((_, currentRecordIndex) => currentRecordIndex !== recordIndex)
            : section.records,
      })),
    }));
  };

  const addRecord = (sectionIndex: number) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? [
                ...section.records,
                { target: "", raw_changes: [""], bbox: [], ocr_confidence: 1 },
              ]
            : section.records,
      })),
    }));
  };

  const updateSectionLabel = (sectionIndex: number, label: string) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) =>
        currentSectionIndex === sectionIndex ? { ...section, label } : section,
      ),
    }));
  };

  useEffect(() => {
    const normalizedSections = normalizeOCRSections(tableData.sections ?? []);
    const sourceNormalizedSections = normalizeOCRSections(sourceTable?.sections ?? []);
    const dirty =
      JSON.stringify(normalizedSections) !== JSON.stringify(sourceNormalizedSections);
    const invalid = normalizedSections.some(
      (section) =>
        !section.label ||
        !section.records.length ||
        section.records.some((record) => !record.target),
    );
    onDraftChange({
      extractionId: extraction.id,
      tableData: {
        ...tableData,
        preview_kind: tableData.preview_kind ?? "preview",
        structure_confidence: 1,
        sections: normalizedSections,
      },
      dirty,
      invalid,
    });
  }, [extraction.id, onDraftChange, sourceTable, tableData]);

  if (!sourceTable || !sections.length) {
    return (
      <div className="ocr-review-editor">
        <strong>OCR 人工修订</strong>
        <p>这条图片提取没有可编辑的表格结构，当前只能直接批准审核。</p>
      </div>
    );
  }

  return (
    <section className="ocr-review-editor">
      <div className="ocr-review-heading">
        <div>
          <strong>OCR 人工修订 · 提取 #{extraction.id}</strong>
          <p>直接修改识别结果；有改动时，使用审核卡片底部的按钮保存并退回重新处理。</p>
        </div>
        <span>{extraction.schema_version}</span>
      </div>
      <div className="ocr-group-tabs" role="tablist" aria-label="OCR 分组">
        {sections.map((section, sectionIndex) => (
          <button
            className={sectionIndex === activeSectionIndex ? "active" : ""}
            key={`${section.section_type}-${sectionIndex}`}
            type="button"
            role="tab"
            aria-selected={sectionIndex === activeSectionIndex}
            onClick={() => {
              setActiveSectionIndex(sectionIndex);
              setActiveRecordIndex(0);
            }}
          >
            <span>{section.label || `分组 ${sectionIndex + 1}`}</span>
            <b>{section.records.length}</b>
          </button>
        ))}
      </div>
      <div className="ocr-review-workspace">
        <figure className="ocr-reference-image">
          <figcaption>
            <span>原图对照</span>
            <small>{asset ? `MEDIA #${asset.media_asset_id}` : "未找到媒体信息"}</small>
          </figcaption>
          {asset?.storage_path ? (
            <button
              className="ocr-reference-trigger"
              type="button"
              aria-label="放大查看 OCR 原图"
              onClick={() => setImageExpanded(true)}
            >
              <Image
                src={asset.storage_path}
                alt={asset.raw_title ?? "Patch Preview OCR 原图"}
                width={asset.width ?? 1200}
                height={asset.height ?? 1600}
                sizes="(max-width: 900px) 100vw, 42vw"
                unoptimized
              />
              <span>点击放大查看</span>
            </button>
          ) : (
            <div className="ocr-reference-missing">原图暂不可用</div>
          )}
        </figure>
        {activeSection && (
          <div className="ocr-review-section">
            <div className="ocr-section-heading">
              <label>
                <span className="ocr-field-label">
                  分组标题
                  <small className={ocrConfidenceClass(activeSectionConfidence)}>
                    标题 OCR {ocrConfidenceValue(activeSectionConfidence)}
                  </small>
                </span>
                <input
                  value={activeSection.label}
                  onChange={(event) =>
                    updateSectionLabel(activeSectionIndex, event.target.value)
                  }
                />
              </label>
              <span>{activeSection.section_type}</span>
            </div>
            <div className="ocr-record-tabs" role="tablist" aria-label="当前分组对象">
              {activeSection.records.map((record, recordIndex) => (
                <button
                  className={recordIndex === activeRecordIndex ? "active" : ""}
                  key={`${activeSectionIndex}-${recordIndex}`}
                  type="button"
                  role="tab"
                  aria-selected={recordIndex === activeRecordIndex}
                  onClick={() => setActiveRecordIndex(recordIndex)}
                >
                  <b>{recordIndex + 1}</b>
                  <span>{record.target || "未命名对象"}</span>
                  <small className={ocrConfidenceClass(record.ocr_confidence)}>
                    {ocrConfidenceValue(record.ocr_confidence)}
                  </small>
                </button>
              ))}
            </div>
            {activeRecord && (
              <div className="ocr-review-record">
                <div className="ocr-record-meta">
                  <span>对象 {activeRecordIndex + 1}</span>
                  <small className={ocrConfidenceClass(activeRecord.ocr_confidence)}>
                    对象与具体改动 OCR 综合置信度{" "}
                    {ocrConfidenceValue(activeRecord.ocr_confidence)}
                  </small>
                </div>
                <label>
                  对象（左栏）
                  <input
                    value={activeRecord.target}
                    onChange={(event) =>
                      updateRecord(activeSectionIndex, activeRecordIndex, {
                        target: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  具体改动（右栏，每行一项）
                  <textarea
                    ref={changesTextareaRef}
                    value={activeRecord.raw_changes.join("\n")}
                    placeholder={
                      tableData.preview_kind === "preview"
                        ? "普通 Preview 可以没有具体改动"
                        : "每行填写一项改动"
                    }
                    onChange={(event) =>
                      updateRecord(activeSectionIndex, activeRecordIndex, {
                        raw_changes: event.target.value.split("\n"),
                      })
                    }
                  />
                </label>
                <button
                  className="text-button danger"
                  type="button"
                  disabled={activeSection.records.length === 1}
                  onClick={() => {
                    removeRecord(activeSectionIndex, activeRecordIndex);
                    setActiveRecordIndex((current) =>
                      Math.max(0, Math.min(current, activeSection.records.length - 2)),
                    );
                  }}
                >
                  删除
                </button>
              </div>
            )}
            <button
              className="text-button add-record"
              type="button"
              onClick={() => {
                addRecord(activeSectionIndex);
                setActiveRecordIndex(activeSection.records.length);
              }}
            >
              添加对象
            </button>
          </div>
        )}
      </div>
      {imageExpanded && asset?.storage_path && (
        <div
          className="ocr-image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="OCR 原图放大查看"
          onClick={() => setImageExpanded(false)}
        >
          <button
            className="ocr-lightbox-close"
            type="button"
            aria-label="关闭原图"
            onClick={() => setImageExpanded(false)}
          >
            <X size={22} />
          </button>
          <div className="ocr-lightbox-image" onClick={(event) => event.stopPropagation()}>
            <Image
              src={asset.storage_path}
              alt={asset.raw_title ?? "Patch Preview OCR 放大原图"}
              width={asset.width ?? 1600}
              height={asset.height ?? 2200}
              sizes="96vw"
              unoptimized
            />
          </div>
        </div>
      )}
    </section>
  );
}

function textValue(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "—";
}

function scoreValue(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)} / 100` : "—";
}

function ocrConfidenceValue(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function ocrConfidenceClass(value: number | null | undefined): string {
  if (typeof value !== "number") return "ocr-confidence unknown";
  if (value < 0.8) return "ocr-confidence low";
  if (value < 0.9) return "ocr-confidence medium";
  return "ocr-confidence high";
}

function findSectionOCRConfidence(
  lines: OCRLine[],
  sectionLabel: string | undefined,
): number | null {
  if (!sectionLabel) return null;
  const normalizedLabel = normalizeOCRLabel(sectionLabel);
  const match = lines.find(
    (line) => normalizeOCRLabel(line.text) === normalizedLabel,
  );
  return match?.confidence ?? null;
}

function normalizeOCRLabel(value: string): string {
  return value
    .normalize("NFKC")
    .toUpperCase()
    .replace(/[^A-Z0-9\u4E00-\u9FFF]/g, "");
}

function entityLabel(value: unknown): string {
  if (!value || typeof value !== "object") return textValue(value);
  const entity = value as Record<string, unknown>;
  const fallback = Object.entries(entity).find(
    ([key, fieldValue]) =>
      !["name", "type", "canonical_name"].includes(key)
      && typeof fieldValue === "string"
      && fieldValue.trim().length > 0,
  );
  const name = textValue(entity.name ?? fallback?.[1]);
  const type = textValue(entity.type ?? fallback?.[0]);
  return type === "—" ? name : `${name} · ${type}`;
}

function normalizeOCRSections(sections: PatchTableSection[]): PatchTableSection[] {
  return sections.map((section) => ({
    ...section,
    label: section.label.trim(),
    records: section.records.map((record) => ({
      ...record,
      target: record.target.trim(),
      raw_changes: record.raw_changes.map((change) => change.trim()).filter(Boolean),
    })),
  }));
}

const defaultOCRParameters: OCRParameters = {
  scale: 1,
  grayscale: false,
  contrast: 1,
  sharpness: 1,
  text_score: null,
  box_thresh: null,
  unclip_ratio: null,
  use_cls: true,
  divider_x_ratio: null,
  line_brightness: 105,
  line_coverage: 0.82,
};

const ocrPresets: Record<string, OCRParameters> = {
  原图默认: defaultOCRParameters,
  小字放大: { ...defaultOCRParameters, scale: 2, sharpness: 1.25 },
  灰度增强: {
    ...defaultOCRParameters,
    scale: 2,
    grayscale: true,
    contrast: 1.35,
    sharpness: 1.2,
  },
};

function OCRLabPanel({
  assets,
  runs,
  profiles,
  busy,
  act,
}: {
  assets: OCRAsset[];
  runs: OCRTestRun[];
  profiles: OCRProfile[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [selectedAssetId, setSelectedAssetId] = useState<number | null>(assets[0]?.media_asset_id ?? null);
  const [selectedRun, setSelectedRun] = useState<OCRTestRun | null>(runs[0] ?? null);
  const [profileName, setProfileName] = useState("原图默认");
  const [parameters, setParameters] = useState<OCRParameters>(defaultOCRParameters);
  const effectiveAssetId = selectedAssetId ?? assets[0]?.media_asset_id ?? null;
  const selectedAsset = assets.find((asset) => asset.media_asset_id === effectiveAssetId);
  const activeProfile = profiles.find((profile) => profile.is_active);
  const assetRuns = runs.filter((run) => run.media_asset_id === effectiveAssetId);

  useEffect(() => {
    if (selectedAssetId === null && assets[0]) {
      setSelectedAssetId(assets[0].media_asset_id);
    }
  }, [assets, selectedAssetId]);

  useEffect(() => {
    if (selectedRun === null && effectiveAssetId !== null) {
      const latestRun = runs.find((run) => run.media_asset_id === effectiveAssetId);
      if (latestRun) setSelectedRun(latestRun);
    }
  }, [effectiveAssetId, runs, selectedRun]);

  const applyPreset = (name: string) => {
    setProfileName(name);
    setParameters({ ...ocrPresets[name] });
  };
  const setNumber = (key: keyof OCRParameters, value: string, optional = false) => {
    setParameters((current) => ({
      ...current,
      [key]: optional && value === "" ? null : Number(value),
    }));
  };

  if (!assets.length) {
    return (
      <section className="admin-empty">
        没有找到 @RiotPhroxzon 已下载到本地的图片。先运行对应 Connector 后再测试。
      </section>
    );
  }

  return (
    <section className="ocr-lab">
      <div className="ocr-toolbar">
        <label>
          测试图片
          <select
            value={effectiveAssetId ?? ""}
            onChange={(event) => {
              setSelectedAssetId(Number(event.target.value));
              setSelectedRun(null);
            }}
          >
            {assets.map((asset) => (
              <option value={asset.media_asset_id} key={asset.media_asset_id}>
                #{asset.media_asset_id} · {asset.raw_title ?? `Raw #${asset.raw_item_id}`} · 图 {asset.block_index + 1}
              </option>
            ))}
          </select>
        </label>
        <div className="ocr-active-profile">
          <span>生产 OCR 参数</span>
          <strong>{activeProfile ? activeProfile.name : "尚未激活，使用引擎默认值"}</strong>
        </div>
      </div>

      <div className="ocr-workbench">
        <aside className="ocr-controls">
          <div className="ocr-presets">
            {Object.keys(ocrPresets).map((name) => (
              <button type="button" key={name} onClick={() => applyPreset(name)}>{name}</button>
            ))}
          </div>
          <label>参数组名称<input value={profileName} onChange={(event) => setProfileName(event.target.value)} /></label>
          <div className="ocr-control-grid">
            <label>缩放倍数<input type="number" min="1" max="4" step="0.25" value={parameters.scale} onChange={(event) => setNumber("scale", event.target.value)} /></label>
            <label>对比度<input type="number" min="0.5" max="3" step="0.05" value={parameters.contrast} onChange={(event) => setNumber("contrast", event.target.value)} /></label>
            <label>锐度<input type="number" min="0.5" max="3" step="0.05" value={parameters.sharpness} onChange={(event) => setNumber("sharpness", event.target.value)} /></label>
            <label>文本分数阈值<input type="number" min="0" max="1" step="0.05" value={parameters.text_score ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("text_score", event.target.value, true)} /></label>
            <label>检测框阈值<input type="number" min="0" max="1" step="0.05" value={parameters.box_thresh ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("box_thresh", event.target.value, true)} /></label>
            <label>检测框扩张<input type="number" min="0.5" max="3" step="0.1" value={parameters.unclip_ratio ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("unclip_ratio", event.target.value, true)} /></label>
            <label>分隔线位置比例<input type="number" min="0.1" max="0.4" step="0.005" value={parameters.divider_x_ratio ?? ""} placeholder="自动检测" onChange={(event) => setNumber("divider_x_ratio", event.target.value, true)} /></label>
            <label>表格线亮度<input type="number" min="40" max="220" step="1" value={parameters.line_brightness} onChange={(event) => setNumber("line_brightness", event.target.value)} /></label>
            <label>横线覆盖率<input type="number" min="0.5" max="1" step="0.01" value={parameters.line_coverage} onChange={(event) => setNumber("line_coverage", event.target.value)} /></label>
          </div>
          <div className="ocr-checks">
            <label><input type="checkbox" checked={parameters.grayscale} onChange={(event) => setParameters((current) => ({ ...current, grayscale: event.target.checked }))} /> 灰度化</label>
            <label><input type="checkbox" checked={parameters.use_cls} onChange={(event) => setParameters((current) => ({ ...current, use_cls: event.target.checked }))} /> 文字方向分类</label>
          </div>
          <button
            className="ocr-run-button"
            type="button"
            disabled={!effectiveAssetId || busy === "ocr-run"}
            onClick={() =>
              void act(
                "ocr-run",
                () =>
                  api<OCRTestRun>("/ocr-lab/runs", {
                    method: "POST",
                    body: JSON.stringify({
                      media_asset_id: effectiveAssetId,
                      profile_name: profileName,
                      parameters,
                    }),
                  }).then((result) => {
                    setSelectedRun(result);
                    return result;
                  }),
                "OCR 测试完成；结果已保存，未调用 LLM",
              )
            }
          >
            {busy === "ocr-run" ? <LoaderCircle className="spin" size={14} /> : <ScanText size={14} />}
            运行本地 OCR
          </button>
          {assetRuns.length > 0 && (
            <div className="ocr-history">
              <span>这张图的历史结果</span>
              {assetRuns.map((run) => (
                <button type="button" key={run.id} onClick={() => setSelectedRun(run)}>
                  #{run.id} · {run.profile_name} ·
                  {run.structure_confidence === null
                    ? " 旧版"
                    : ` 结构 ${(run.structure_confidence * 100).toFixed(1)}%`}
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="ocr-results">
          <div className="ocr-images">
            <figure>
              <figcaption>原图 · {selectedAsset?.width ?? "?"} × {selectedAsset?.height ?? "?"}</figcaption>
              {selectedAsset && (
                <Image
                  src={selectedAsset.storage_path}
                  alt="OCR 测试原图"
                  width={selectedAsset.width ?? 1200}
                  height={selectedAsset.height ?? 800}
                  unoptimized
                />
              )}
            </figure>
            <figure>
              <figcaption>
                识别框
                {selectedRun && ` · ${selectedRun.processed_width} × ${selectedRun.processed_height}`}
              </figcaption>
              {selectedRun?.overlay_path
                ? (
                    <Image
                      src={selectedRun.overlay_path}
                      alt="OCR 识别框叠加结果"
                      width={selectedRun.processed_width}
                      height={selectedRun.processed_height}
                      unoptimized
                    />
                  )
                : <div className="ocr-placeholder">运行后在这里检查每个识别框</div>}
            </figure>
            <figure>
              <figcaption>
                表格单元格
                {selectedRun?.structure_confidence !== null
                  && selectedRun?.structure_confidence !== undefined
                  && ` · 结构 ${(selectedRun.structure_confidence * 100).toFixed(1)}%`}
              </figcaption>
              {selectedRun?.table_overlay_path
                ? (
                    <Image
                      src={selectedRun.table_overlay_path}
                      alt="表格结构与键值配对叠加结果"
                      width={selectedRun.processed_width}
                      height={selectedRun.processed_height}
                      unoptimized
                    />
                  )
                : <div className="ocr-placeholder">新版测试会在这里标出分隔线和合并单元格</div>}
            </figure>
          </div>

          {selectedRun && (
            <>
              <div className="ocr-result-meta">
                <span>结果 #{selectedRun.id}</span>
                <strong>平均置信度 {(selectedRun.confidence * 100).toFixed(2)}%</strong>
                {selectedRun.structure_confidence !== null && (
                  <strong>结构置信度 {(selectedRun.structure_confidence * 100).toFixed(2)}%</strong>
                )}
                {selectedRun.table_data.preview_kind && (
                  <span>
                    {selectedRun.table_data.preview_kind === "full_preview" ? "Full Preview" : "Preview"}
                    {selectedRun.table_data.divider_x
                      ? ` · 分隔线 x=${selectedRun.table_data.divider_x}`
                      : " · 无详情列"}
                  </span>
                )}
                <span>{selectedRun.engine}</span>
                <button
                  type="button"
                  disabled={
                    busy === `ocr-activate-${selectedRun.id}`
                    || selectedRun.structure_confidence === null
                    || selectedRun.structure_confidence < 0.65
                  }
                  onClick={() =>
                    void act(
                      `ocr-activate-${selectedRun.id}`,
                      () => api(`/ocr-lab/runs/${selectedRun.id}/activate`, { method: "POST" }),
                      `参数组“${selectedRun.profile_name}”已设为生产 OCR 参数`,
                    )
                  }
                >
                  设为生产参数
                </button>
              </div>
              {selectedRun.table_data.warnings && selectedRun.table_data.warnings.length > 0 && (
                <div className="ocr-structure-warnings">
                  {selectedRun.table_data.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                </div>
              )}
              {selectedRun.table_data.sections && selectedRun.table_data.sections.length > 0 && (
                <div className="ocr-pairs">
                  <div className="panel-title">
                    <h2>表格键值配对</h2>
                    <span>
                      {selectedRun.table_data.sections.reduce(
                        (total, section) => total + section.records.length,
                        0,
                      )} 个目标
                    </span>
                  </div>
                  {selectedRun.table_data.sections.map((section) => (
                    <section className="ocr-pair-section" key={`${selectedRun.id}-${section.section_type}`}>
                      <h3>{section.label} <span>{section.section_type}</span></h3>
                      {section.records.map((record) => (
                        <article className="ocr-pair" key={`${section.section_type}-${record.target}`}>
                          <strong>{record.target}</strong>
                          <div>
                            {record.raw_changes.length > 0
                              ? record.raw_changes.map((change, index) => (
                                  <p key={`${record.target}-${index}`}>{change}</p>
                                ))
                              : (
                                  <p className="ocr-no-change">
                                    {selectedRun.table_data.preview_kind === "preview"
                                      ? "Preview 仅公布目标，尚无具体数值"
                                      : "未识别到右侧改动，需要人工检查"}
                                  </p>
                                )}
                          </div>
                          <small>{(record.ocr_confidence * 100).toFixed(1)}%</small>
                        </article>
                      ))}
                    </section>
                  ))}
                </div>
              )}
              <div className="ocr-lines">
                <div className="ocr-line ocr-line-head"><span>#</span><span>识别文本</span><span>置信度</span></div>
                {selectedRun.lines.map((line) => (
                  <div className="ocr-line" key={`${selectedRun.id}-${line.index}`}>
                    <span>{line.index}</span>
                    <span>{line.text}</span>
                    <span>{line.confidence === null ? "—" : `${(line.confidence * 100).toFixed(1)}%`}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function KnowledgePanel({
  rules,
  terms,
  busy,
  act,
}: {
  rules: KnowledgeRule[];
  terms: GlossaryTerm[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  return (
    <section className="knowledge-grid">
      <div>
        <div className="panel-title">
          <h2>判断规则</h2>
          <div className="panel-title-actions">
            <span>{rules.filter((rule) => rule.is_active).length} 条生效</span>
            <button
              type="button"
              disabled={
                !rules.some((rule) => rule.is_active) ||
                busy === "organize-knowledge"
              }
              onClick={() =>
                void act(
                  "organize-knowledge",
                  () => api("/knowledge/rules/organize", { method: "POST" }),
                  "AI 已完成知识去重、合并和精简；原规则已保留为停用历史",
                )
              }
            >
              <Sparkles size={13} />
              {busy === "organize-knowledge" ? "正在整理…" : "AI 整理全部知识"}
            </button>
          </div>
        </div>
        {rules.map((rule) => (
          <EditableRule rule={rule} busy={busy} act={act} key={rule.id} />
        ))}
      </div>
      <div>
        <div className="panel-title"><h2>翻译术语</h2><span>{terms.length} 条</span></div>
        {terms.map((term) => (
          <EditableTerm term={term} busy={busy} act={act} key={term.id} />
        ))}
      </div>
    </section>
  );
}

function EditableRule({
  rule,
  busy,
  act,
}: {
  rule: KnowledgeRule;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [text, setText] = useState(rule.rule_text);
  return (
    <article className={`knowledge-card ${rule.is_active ? "" : "inactive"}`}>
      <span>{rule.knowledge_type} · {rule.scope} · v{rule.version}</span>
      <textarea value={text} onChange={(event) => setText(event.target.value)} />
      <div className="admin-actions">
        <button type="button" disabled={busy === `rule-${rule.id}`} onClick={() => void act(
          `rule-${rule.id}`,
          () => api(`/knowledge/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ rule_text: text }) }),
          "规则已更新",
        )}>保存</button>
        <button type="button" onClick={() => void act(
          `rule-toggle-${rule.id}`,
          () => api(`/knowledge/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !rule.is_active }) }),
          rule.is_active ? "规则已停用" : "规则已启用",
        )}>{rule.is_active ? "停用" : "启用"}</button>
      </div>
    </article>
  );
}

function EditableTerm({
  term,
  busy,
  act,
}: {
  term: GlossaryTerm;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [translation, setTranslation] = useState(term.preferred_translation);
  return (
    <article className={`knowledge-card term-card ${term.is_active ? "" : "inactive"}`}>
      <span>{term.scope} · v{term.version}</span>
      <strong>{term.source_term}</strong>
      <input value={translation} onChange={(event) => setTranslation(event.target.value)} />
      <div className="admin-actions">
        <button type="button" disabled={busy === `term-${term.id}`} onClick={() => void act(
          `term-${term.id}`,
          () => api(`/knowledge/glossary/${term.id}`, { method: "PATCH", body: JSON.stringify({ preferred_translation: translation }) }),
          "术语已更新",
        )}>保存</button>
        <button type="button" onClick={() => void act(
          `term-toggle-${term.id}`,
          () => api(`/knowledge/glossary/${term.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !term.is_active }) }),
          term.is_active ? "术语已停用" : "术语已启用",
        )}>{term.is_active ? "停用" : "启用"}</button>
      </div>
    </article>
  );
}
