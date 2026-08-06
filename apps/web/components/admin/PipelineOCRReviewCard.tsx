"use client";

import Image from "next/image";
import { useState } from "react";
import { adminApi } from "@/lib/api";
import type {
  OCRReviewExtraction,
  OCRTableData,
  OCRWorkflowReview,
} from "@/lib/types";

function confidenceLabel(value: number | null): string {
  return value === null ? "未知" : `${(value * 100).toFixed(1)}%`;
}

function confidenceLevel(
  value: number | null,
): "high" | "medium" | "low" | "unknown" {
  if (value === null) return "unknown";
  if (value >= 0.95) return "high";
  if (value >= 0.8) return "medium";
  return "low";
}

function ConfidenceMark({
  value,
  label,
}: {
  value: number | null;
  label: string;
}) {
  return (
    <span
      className={`ocr-confidence-mark ${confidenceLevel(value)}`}
      title={`${label}：${confidenceLabel(value)}`}
      aria-label={`${label}：${confidenceLabel(value)}`}
    >
      {value === null ? "—" : (value * 100).toFixed(1)}
    </span>
  );
}

function cloneTableData(tableData: OCRTableData): OCRTableData {
  return JSON.parse(JSON.stringify(tableData)) as OCRTableData;
}

function ExtractionPreview({
  extraction,
  review,
  busy,
  onBusy,
  onError,
  onResolved,
}: {
  extraction: OCRReviewExtraction;
  review: OCRWorkflowReview;
  busy: string | null;
  onBusy: (value: string | null) => void;
  onError: (value: string | null) => void;
  onResolved: () => void;
}) {
  const [tableDraft, setTableDraft] = useState<OCRTableData>(() =>
    cloneTableData(extraction.table_data),
  );
  const sectionTypes = Array.from(
    new Set(tableDraft.sections.map((section) => section.section_type)),
  );
  const [activeSectionType, setActiveSectionType] = useState(
    sectionTypes[0] ?? "",
  );
  const [editingRecord, setEditingRecord] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const imageSource = extraction.storage_path ?? extraction.source_url;
  const activeSections = tableDraft.sections
    .map((section, sectionIndex) => ({ section, sectionIndex }))
    .filter(({ section }) => section.section_type === activeSectionType);

  const updateRecord = (
    sectionIndex: number,
    recordIndex: number,
    field: "target" | "raw_changes",
    value: string,
  ) => {
    setTableDraft((current) => {
      const next = cloneTableData(current);
      const record = next.sections[sectionIndex].records[recordIndex];
      if (field === "target") {
        record.target = value;
      } else {
        record.raw_changes = value
          .split("\n")
          .map((line) => line.trim())
          .filter(Boolean);
      }
      return next;
    });
    setDirty(true);
  };

  const saveCorrection = async () => {
    onBusy(`correct-${extraction.id}`);
    onError(null);
    try {
      await adminApi(
        `/workflows/reviews/${review.review_id}/correct-ocr`,
        {
          method: "POST",
          body: JSON.stringify({
            extraction_id: extraction.id,
            table_data: tableDraft,
          }),
        },
      );
      onResolved();
    } catch (value) {
      onError(
        value instanceof Error
          ? value.message
          : "OCR 修正保存失败",
      );
    } finally {
      onBusy(null);
    }
  };

  return (
    <section className="pipeline-ocr-extraction">
      <div className="pipeline-ocr-media">
        {imageSource ? (
          <figure>
            <Image
              src={imageSource}
              alt={`Raw #${review.raw_item_id} OCR 原图`}
              width={960}
              height={720}
              unoptimized
              priority
            />
            <figcaption>
              图片 #{extraction.media_asset_id} · 内容块{" "}
              {extraction.block_index + 1}
            </figcaption>
          </figure>
        ) : (
          <div className="admin-empty">这条提取没有可显示的图片地址。</div>
        )}
        <details className="pipeline-ocr-details">
          <summary>OCR 原始文本</summary>
          <pre>{extraction.raw_ocr_text || "未识别到文字"}</pre>
        </details>
      </div>

      <div className="pipeline-ocr-structure">
        <div className="admin-result-meta">
          <span>Extraction #{extraction.id}</span>
          <span>
            置信度
            <ConfidenceMark value={extraction.confidence} label="OCR 置信度" />
          </span>
          <span>
            结构置信度
            <ConfidenceMark
              value={extraction.table_data.structure_confidence}
              label="结构置信度"
            />
          </span>
        </div>
        <div
          className="pipeline-ocr-section-tabs"
          role="tablist"
          aria-label="OCR 分组"
        >
          {sectionTypes.map((sectionType) => {
            const sections = tableDraft.sections.filter(
              (section) => section.section_type === sectionType,
            );
            const recordCount = sections.reduce(
              (total, section) => total + section.records.length,
              0,
            );
            const label =
              sections[0]?.label.replace(/^\d+\s*/, "") || sectionType;
            return (
              <button
                type="button"
                role="tab"
                aria-selected={activeSectionType === sectionType}
                className={activeSectionType === sectionType ? "active" : ""}
                onClick={() => {
                  setActiveSectionType(sectionType);
                  setEditingRecord(null);
                }}
                key={sectionType}
              >
                <strong>{label}</strong>
                <span>{sectionType}</span>
                <b>{recordCount}</b>
              </button>
            );
          })}
        </div>
        <div className="pipeline-ocr-section-panel" role="tabpanel">
          {activeSections.flatMap(({ section, sectionIndex }) =>
            section.records.map((record, recordIndex) => {
              const recordKey = `${sectionIndex}:${recordIndex}`;
              const isEditing = editingRecord === recordKey;
              return (
                <article
                  className={`pipeline-ocr-record${
                    isEditing ? " editing" : ""
                  }`}
                  key={recordKey}
                >
                  {isEditing ? (
                    <>
                      <label>
                        对象名称
                        <input
                          autoFocus
                          value={record.target}
                          onChange={(event) =>
                            updateRecord(
                              sectionIndex,
                              recordIndex,
                              "target",
                              event.target.value,
                            )
                          }
                        />
                      </label>
                      <label>
                        具体改动（每行一条）
                        <textarea
                          value={record.raw_changes.join("\n")}
                          onChange={(event) =>
                            updateRecord(
                              sectionIndex,
                              recordIndex,
                              "raw_changes",
                              event.target.value,
                            )
                          }
                        />
                      </label>
                      <button
                        type="button"
                        onClick={() => setEditingRecord(null)}
                      >
                        完成编辑
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setEditingRecord(recordKey)}
                    >
                      <span className="pipeline-ocr-record-title">
                        <ConfidenceMark
                          value={record.ocr_confidence}
                          label={`${record.target} OCR 置信度`}
                        />
                        <b>{record.target}</b>
                      </span>
                      {record.raw_changes.length > 0 && (
                        <span>{record.raw_changes.join("；")}</span>
                      )}
                    </button>
                  )}
                </article>
              );
            }),
          )}
        </div>
        <p className="pipeline-ocr-edit-hint">
          点击任意条目可直接修改名称和具体改动；色块表示该项 OCR
          置信度。
        </p>
        <details className="pipeline-ocr-details">
          <summary>结构化 JSON 原文</summary>
          <pre>{JSON.stringify(extraction.table_data, null, 2)}</pre>
        </details>
        {dirty && (
          <button
            className="pipeline-ocr-save"
            type="button"
            disabled={busy !== null}
            onClick={() => void saveCorrection()}
          >
            {busy === `correct-${extraction.id}`
              ? "保存中…"
              : "保存 OCR 修正"}
          </button>
        )}
        {tableDraft.warnings.length > 0 && (
          <div className="admin-error-state">
            <span>{tableDraft.warnings.join("；")}</span>
          </div>
        )}
      </div>
    </section>
  );
}

export function PipelineOCRReviewCard({
  review,
  onResolved,
}: {
  review: OCRWorkflowReview;
  onResolved: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const act = async (action: "approve" | "reject") => {
    setBusy(action);
    setError(null);
    try {
      await adminApi(
        `/workflows/reviews/${review.review_id}/${action}`,
        {
          method: "POST",
          body: JSON.stringify(
            action === "approve"
              ? { note: null }
              : { feedback_type: "ocr_error", reason: null },
          ),
        },
      );
      onResolved();
    } catch (value) {
      setError(value instanceof Error ? value.message : "OCR 审核操作失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <article className="admin-review-card pipeline-ocr-review-card">
      <header>
        <div>
          <span>图片 OCR 审核</span>
          <strong>审核 #{review.review_id}</strong>
        </div>
        <b>Raw #{review.raw_item_id}</b>
      </header>
      <div className="pipeline-ocr-title">
        <strong>{review.raw_title ?? `消息 #${review.raw_item_id}`}</strong>
        <span>Processing Run #{review.processing_run_id}</span>
      </div>
      {review.corrections.length > 0 && (
        <p className="admin-inline-note">
          已保存 {review.corrections.length} 次 OCR 人工修正
        </p>
      )}
      {review.extractions.map((extraction) => (
        <ExtractionPreview
          key={extraction.id}
          extraction={extraction}
          review={review}
          busy={busy}
          onBusy={setBusy}
          onError={setError}
          onResolved={onResolved}
        />
      ))}
      {review.extractions.length === 0 && (
        <div className="admin-error-state">
          <span>审核引用的 OCR 提取记录不存在，不能批准。</span>
        </div>
      )}
      <div className="admin-inline-actions">
        <button
          type="button"
          disabled={busy !== null || review.extractions.length === 0}
          onClick={() => void act("approve")}
        >
          {busy === "approve" ? "批准中…" : "确认 OCR 并继续"}
        </button>
        <button
          className="danger"
          type="button"
          disabled={busy !== null}
          onClick={() => void act("reject")}
        >
          {busy === "reject" ? "退回中…" : "退回重新处理"}
        </button>
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
    </article>
  );
}
