"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "@/lib/api";
import type { ReviewQueueItem } from "@/lib/types";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { PipelineOCRReviewCard } from "@/components/admin/PipelineOCRReviewCard";
import { ReviewCard } from "@/components/admin/ReviewCard";

const pipelineStages = [
  "relevance",
  "image_ocr",
  "translation",
  "fact_classify",
  "importance",
  "claim_gen",
  "event_decision",
] as const;

const stageLabels: Record<string, string> = {
  relevance: "相关性",
  image_ocr: "图片 OCR",
  translation: "翻译",
  fact_classify: "事实与分类",
  importance: "重要性",
  claim_gen: "断言生成",
  event_decision: "事件归属",
};

function ReviewProgress({ item }: { item: ReviewQueueItem }) {
  const currentIndex = pipelineStages.indexOf(
    item.current_stage as (typeof pipelineStages)[number],
  );
  const completed = new Set(item.completed_stages);
  return (
    <div
      className="review-queue-progress"
      aria-label={`当前待审阶段：${stageLabels[item.current_stage] ?? item.current_stage}`}
    >
      {pipelineStages.map((stage, index) => {
        const status = completed.has(stage)
          ? "done"
          : stage === item.current_stage
            ? "review"
            : currentIndex >= 0 && index < currentIndex
              ? "done"
              : "pending";
        return (
          <div className={`review-progress-step ${status}`} key={stage}>
            <span>{status === "done" ? "✓" : status === "review" ? "Ⅱ" : ""}</span>
            <small>{stageLabels[stage]}</small>
          </div>
        );
      })}
    </div>
  );
}

function ReviewDialog({
  item,
  onClose,
  onResolved,
}: {
  item: ReviewQueueItem;
  onClose: () => void;
  onResolved: () => void;
}) {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const resolved = () => {
    onClose();
    onResolved();
  };

  return (
    <div className="admin-editor-backdrop" onMouseDown={onClose}>
      <section
        className="review-stage-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="review-dialog-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="review-dialog-head">
          <div>
            <span>RAW #{item.raw_item_id}</span>
            <h2 id="review-dialog-title">
              {stageLabels[item.current_stage] ?? item.current_stage}审核
            </h2>
            <p>{item.raw_title ?? `消息 #${item.raw_item_id}`}</p>
          </div>
          <div className="review-dialog-actions">
            {item.canonical_url ? (
              <a
                href={item.canonical_url}
                target="_blank"
                rel="noreferrer"
              >
                前往原文 ↗
              </a>
            ) : (
              <span>无原文链接</span>
            )}
            <button type="button" onClick={onClose} aria-label="关闭审核窗口">
              ×
            </button>
          </div>
        </header>
        <div className="review-dialog-context">
          <b>Raw ID：{item.raw_item_id}</b>
          <span>信源：{item.source_name}</span>
          {item.processing_run_id && (
            <span>Processing Run #{item.processing_run_id}</span>
          )}
          {item.normalized_item_id && (
            <span>Normalized Item #{item.normalized_item_id}</span>
          )}
        </div>
        <div className="review-dialog-body">
          {item.review_kind === "ocr" && item.ocr_review ? (
            <PipelineOCRReviewCard
              review={item.ocr_review}
              onResolved={resolved}
            />
          ) : item.review_kind === "event" && item.event_review ? (
            <ReviewCard
              review={item.event_review}
              kind="event"
              onResolved={resolved}
            />
          ) : item.message_review ? (
            <ReviewCard
              review={item.message_review}
              kind="message"
              onResolved={resolved}
            />
          ) : (
            <div className="admin-error-state">
              <span>该待审项缺少审核内容，请刷新后重试。</span>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}

export default function ReviewsPage() {
  const [items, setItems] = useState<ReviewQueueItem[]>([]);
  const [selected, setSelected] = useState<ReviewQueueItem | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sort, setSort] = useState<"asc" | "desc">("desc");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setItems(await adminApi<ReviewQueueItem[]>("/workflows/review-queue"));
    } catch (value) {
      setError(value instanceof Error ? value.message : "待审队列加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const ordered = useMemo(
    () =>
      [...items].sort(
        (left, right) =>
          (sort === "desc" ? -1 : 1) *
          left.created_at.localeCompare(right.created_at),
      ),
    [items, sort],
  );
  const visible = ordered.slice((page - 1) * pageSize, page * pageSize);

  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">REVIEW PIPELINE</span>
          <h1>审核中心</h1>
          <p>
            按消息列出当前人工检查点；查看完整处理进度后，打开对应阶段完成审核。
          </p>
        </div>
        <strong className="review-queue-total">{items.length} 条待审</strong>
      </header>

      {error && (
        <div className="admin-error-state">
          <span>{error}</span>
          <button onClick={() => void load()}>重试</button>
        </div>
      )}
      {loading ? (
        <div className="admin-skeleton-grid">
          {Array.from({ length: 3 }, (_, index) => (
            <div className="admin-skeleton admin-skeleton-card" key={index} />
          ))}
        </div>
      ) : (
        <>
          <div className="review-queue-list">
            {visible.map((item) => (
              <article
                className="review-queue-row"
                key={`${item.review_kind}-${item.raw_item_id}-${item.created_at}`}
              >
                <div className="review-queue-identity">
                  <span>RAW #{item.raw_item_id}</span>
                  <strong>{item.raw_title ?? `消息 #${item.raw_item_id}`}</strong>
                  <small>{item.source_name}</small>
                </div>
                <ReviewProgress item={item} />
                <div className="review-queue-action">
                  <span>等待{stageLabels[item.current_stage] ?? item.current_stage}</span>
                  <button type="button" onClick={() => setSelected(item)}>
                    审核此阶段
                  </button>
                </div>
              </article>
            ))}
            {!items.length && (
              <div className="admin-empty">当前没有待审消息。</div>
            )}
          </div>
          <PaginationControls
            page={page}
            pageSize={pageSize}
            total={items.length}
            sort={sort}
            onPageChange={setPage}
            onPageSizeChange={(value) => {
              setPageSize(value);
              setPage(1);
            }}
            onSortChange={(value) => {
              setSort(value);
              setPage(1);
            }}
          />
        </>
      )}
      {selected && (
        <ReviewDialog
          item={selected}
          onClose={() => setSelected(null)}
          onResolved={() => void load()}
        />
      )}
    </div>
  );
}
