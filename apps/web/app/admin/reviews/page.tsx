"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "@/lib/api";
import type { EventReviewTask, ReviewTask } from "@/lib/types";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { ReviewCard } from "@/components/admin/ReviewCard";

type OCRRun = {
  id: number;
  media_asset_id: number;
  status: string;
  raw_text: string;
  confidence: number;
  overlay_path: string | null;
  created_at: string;
};
type Tab = "messages" | "events" | "ocr";

export default function ReviewsPage() {
  const [messages, setMessages] = useState<ReviewTask[]>([]);
  const [events, setEvents] = useState<EventReviewTask[]>([]);
  const [ocr, setOcr] = useState<OCRRun[]>([]);
  const [tab, setTab] = useState<Tab>("messages");
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [sort, setSort] = useState<"asc" | "desc">("desc");
  const load = useCallback(async () => {
    setLoading(true);
    const results = await Promise.allSettled([
      adminApi<ReviewTask[]>("/workflows/reviews?status=pending"),
      adminApi<EventReviewTask[]>("/event-workflows/reviews?status=pending"),
      adminApi<OCRRun[]>("/ocr-lab/runs"),
    ]);
    const nextErrors: string[] = [];
    if (results[0].status === "fulfilled") setMessages(results[0].value);
    else nextErrors.push("消息审核队列加载失败");
    if (results[1].status === "fulfilled") setEvents(results[1].value);
    else nextErrors.push("事件审核队列加载失败");
    if (results[2].status === "fulfilled")
      setOcr(
        results[2].value.filter((run) =>
          ["pending", "awaiting_review"].includes(run.status),
        ),
      );
    else nextErrors.push("OCR 队列加载失败");
    setErrors(nextErrors);
    setLoading(false);
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const orderedMessages = useMemo(
    () =>
      [...messages].sort(
        (left, right) =>
          (sort === "desc" ? -1 : 1) *
          left.created_at.localeCompare(right.created_at),
      ),
    [messages, sort],
  );
  const orderedEvents = useMemo(
    () =>
      [...events].sort(
        (left, right) =>
          (sort === "desc" ? -1 : 1) *
          left.created_at.localeCompare(right.created_at),
      ),
    [events, sort],
  );
  const orderedOcr = useMemo(
    () =>
      [...ocr].sort(
        (left, right) =>
          (sort === "desc" ? -1 : 1) *
          left.created_at.localeCompare(right.created_at),
      ),
    [ocr, sort],
  );
  const slice = <T,>(rows: T[]) =>
    rows.slice((page - 1) * pageSize, page * pageSize);
  const total =
    tab === "messages"
      ? messages.length
      : tab === "events"
        ? events.length
        : ocr.length;
  const switchTab = (value: Tab) => {
    setTab(value);
    setPage(1);
  };
  const pagination = (
    <PaginationControls
      page={page}
      pageSize={pageSize}
      total={total}
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
  );
  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">HUMAN CHECKPOINTS</span>
          <h1>审核中心</h1>
          <p>集中处理消息分析、事件归属与 OCR 人工检查点。</p>
        </div>
      </header>
      <div className="admin-queue-tabs">
        <button
          className={tab === "messages" ? "active" : ""}
          onClick={() => switchTab("messages")}
        >
          消息分析待审 <b>{messages.length}</b>
        </button>
        <button
          className={tab === "events" ? "active" : ""}
          onClick={() => switchTab("events")}
        >
          事件归属待审 <b>{events.length}</b>
        </button>
        <button
          className={tab === "ocr" ? "active" : ""}
          onClick={() => switchTab("ocr")}
        >
          OCR 待验 <b>{ocr.length}</b>
        </button>
      </div>
      {errors.map((error) => (
        <div className="admin-error-state" key={error}>
          <span>{error}</span>
          <button onClick={() => void load()}>重试</button>
        </div>
      ))}
      {loading ? (
        <div className="admin-skeleton-grid">
          {Array.from({ length: 3 }, (_, index) => (
            <div className="admin-skeleton admin-skeleton-card" key={index} />
          ))}
        </div>
      ) : tab === "messages" ? (
        <>
          <div className="admin-review-list">
            {slice(orderedMessages).map((review) => (
              <ReviewCard
                key={review.id}
                review={review}
                kind="message"
                onResolved={() => void load()}
              />
            ))}
            {!messages.length && (
              <div className="admin-empty">消息分析队列已清空。</div>
            )}
          </div>
          {pagination}
        </>
      ) : tab === "events" ? (
        <>
          <div className="admin-review-list">
            {slice(orderedEvents).map((review) => (
              <ReviewCard
                key={review.id}
                review={review}
                kind="event"
                onResolved={() => void load()}
              />
            ))}
            {!events.length && (
              <div className="admin-empty">事件归属队列已清空。</div>
            )}
          </div>
          {pagination}
        </>
      ) : (
        <>
          <div className="admin-review-list">
            {slice(orderedOcr).map((run) => (
              <article className="admin-review-card" key={run.id}>
                <header>
                  <strong>OCR Run #{run.id}</strong>
                  <b>confidence {run.confidence.toFixed(2)}</b>
                </header>
                <pre>{run.raw_text}</pre>
                <Link className="admin-primary-button" href="/admin/system/ocr">
                  前往 OCR 测试台修正
                </Link>
              </article>
            ))}
            {!ocr.length && (
              <div className="admin-empty">没有待验 OCR 结果。</div>
            )}
          </div>
          {pagination}
        </>
      )}
    </div>
  );
}
