"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { adminApi } from "@/lib/api";
import type { PublishedItemPage } from "@/lib/types";
import { ContentBlocks } from "@/components/admin/ContentBlocks";
import { ImportanceDimensions } from "@/components/admin/ImportanceDimensions";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { pointScore, relativeTime } from "@/components/admin/admin-utils";

const emptyPage: PublishedItemPage = {
  items: [],
  total: 0,
  topic_options: [],
  subtopic_options: [],
  information_stage_options: [],
};

export default function MessagesPage() {
  const [data, setData] = useState<PublishedItemPage>(emptyPage);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"list" | "review">("list");
  const [topic, setTopic] = useState("all");
  const [type, setType] = useState("all");
  const [importanceSort, setImportanceSort] = useState<"none" | "asc" | "desc">(
    "none",
  );
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [reviewIndex, setReviewIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sort, setSort] = useState<"asc" | "desc">("desc");
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setQuery(search.trim());
      setPage(1);
    }, 300);
    return () => window.clearTimeout(timer);
  }, [search]);
  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    const sortBy = importanceSort !== "none" ? "priority" : "time";
    const direction = importanceSort !== "none" ? importanceSort : sort;
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
      sort_by: sortBy,
      sort: direction,
    });
    if (topic !== "all") params.set("primary_topic", topic);
    if (type !== "all") params.set("subtopic", type);
    if (query) params.set("search", query);
    try {
      setData(
        await adminApi<PublishedItemPage>(
          `/normalized-items/published-page?${params}`,
        ),
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "消息加载失败");
    } finally {
      setLoading(false);
    }
  }, [
    importanceSort,
    page,
    pageSize,
    query,
    sort,
    topic,
    type,
  ]);
  useEffect(() => {
    void load();
  }, [load]);
  useEffect(() => {
    setReviewIndex(0);
  }, [data.items]);
  const current =
    data.items[Math.min(reviewIndex, Math.max(0, data.items.length - 1))];
  const flag = async () => {
    if (!current) return;
    setBusy(true);
    setActionError(null);
    try {
      await adminApi(`/pipeline/normalized-items/${current.id}/corrections`, {
        method: "POST",
        body: JSON.stringify({
          restart_from_stage: "fact_classify",
          resume_mode: "manual",
          reason: "审阅视图标记分析结果有问题",
        }),
      });
    } catch (value) {
      setActionError(value instanceof Error ? value.message : "标记失败");
    } finally {
      setBusy(false);
    }
  };
  const resetPage = (setter: () => void) => {
    setter();
    setPage(1);
  };

  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">CONTENT LIBRARY</span>
          <h1>消息管理</h1>
          <p>共 {data.total} 条已发布消息，默认按发布时间从近到远展示。</p>
        </div>
        <div className="admin-view-toggle">
          <button
            className={view === "list" ? "active" : ""}
            onClick={() => setView("list")}
          >
            列表视图
          </button>
          <button
            className={view === "review" ? "active" : ""}
            onClick={() => setView("review")}
          >
            审阅视图
          </button>
        </div>
      </header>
      <section className="admin-filters admin-message-filters">
        <label>
          Topic
          <select
            value={topic}
            onChange={(event) => resetPage(() => setTopic(event.target.value))}
          >
            <option value="all">全部</option>
            {data.topic_options.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          子主题
          <select
            value={type}
            onChange={(event) => resetPage(() => setType(event.target.value))}
          >
            <option value="all">全部</option>
            {data.subtopic_options.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label>
          展示优先级排序
          <select
            value={importanceSort}
            onChange={(event) => {
              const value = event.target.value as "none" | "asc" | "desc";
              setImportanceSort(value);
              setPage(1);
            }}
          >
            <option value="none">不排序</option>
            <option value="asc">正序（低到高）</option>
            <option value="desc">倒序（高到低）</option>
          </select>
        </label>
        <label>
          时间排序
          <select
            value={
              importanceSort === "none" ? sort : "none"
            }
            onChange={(event) => {
              const value = event.target.value as "asc" | "desc";
              setSort(value);
              setImportanceSort("none");
              setPage(1);
            }}
          >
            <option value="none" disabled>
              按其他指标排序
            </option>
            <option value="asc">正序（最早优先）</option>
            <option value="desc">倒序（最新优先）</option>
          </select>
        </label>
        <label className="admin-search">
          <span>搜索</span>
          <div>
            <Search size={15} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="ID、标题或摘要"
            />
          </div>
        </label>
      </section>
      {error && (
        <div className="admin-error-state">
          <span>{error}</span>
          <button onClick={() => void load()}>重试</button>
        </div>
      )}
      {loading && !data.items.length ? (
        <div className="admin-skeleton admin-skeleton-table" />
      ) : view === "list" ? (
        <>
          <div className="admin-table-scroll">
            <table className="admin-table">
              <thead>
                <tr>
                  <th>ID</th>
                  <th>标题</th>
                  <th>子主题</th>
                  <th>Topic</th>
                  <th>重要性</th>
                  <th>事件</th>
                  <th>发布时间</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => (
                  <tr key={item.id} className="admin-clickable-row">
                    <td className="admin-number">#{item.id}</td>
                    <td>
                      <Link href={`/admin/messages/${item.id}`}>
                        <strong>{item.title}</strong>
                        <small>{item.summary.slice(0, 80)}</small>
                      </Link>
                    </td>
                    <td>
                      <span className="admin-badge">
                        {item.subtopic}
                      </span>
                    </td>
                    <td>
                      <span className="admin-badge subtle">
                        {item.primary_topic}
                      </span>
                    </td>
                    <td>
                      <span
                        className="admin-score importance"
                        style={
                          {
                            "--score": item.importance_score,
                          } as React.CSSProperties
                        }
                      >
                        {pointScore(item.importance_score)}
                      </span>
                    </td>
                    <td className="admin-number">
                      {item.event_memberships?.length ?? 0}
                    </td>
                    <td
                      title={
                        item.published_at
                          ? new Date(item.published_at).toLocaleString("zh-CN")
                          : "未知"
                      }
                    >
                      {relativeTime(item.published_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!data.items.length && (
              <div className="admin-empty">没有符合条件的已发布消息。</div>
            )}
          </div>
          <PaginationControls
            page={page}
            pageSize={pageSize}
            total={data.total}
            sort={sort}
            showSort={false}
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
      ) : current ? (
        <>
          <section className="admin-review-workspace">
            <article>
              <header>
                <span className="admin-badge">消息 #{current.id}</span>
                <span className="admin-badge">{current.source_name}</span>
                <time>
                  {current.published_at
                    ? new Date(current.published_at).toLocaleString("zh-CN")
                    : "未知时间"}
                </time>
              </header>
              <h2>{current.original_title ?? current.title}</h2>
              <ContentBlocks blocks={current.original_content_blocks} />
            </article>
            <article>
              <header>
                <span className="admin-badge">
                  {current.subtopic}
                </span>
                <span className="admin-badge subtle">
                  {current.primary_topic}
                </span>
              </header>
              <h2>{current.title}</h2>
              <p>{current.summary}</p>
              <div className="admin-detail-breakdowns">
                <ImportanceDimensions
                  scoreValue={current.importance_score}
                  dimensions={current.importance_dimensions}
                />
              </div>
              <div className="admin-inline-actions">
                <button
                  disabled={reviewIndex <= 0}
                  onClick={() =>
                    setReviewIndex((value) => Math.max(0, value - 1))
                  }
                >
                  上一条
                </button>
                <button
                  disabled={reviewIndex >= data.items.length - 1}
                  onClick={() =>
                    setReviewIndex((value) =>
                      Math.min(data.items.length - 1, value + 1),
                    )
                  }
                >
                  下一条
                </button>
                <button
                  className="danger"
                  disabled={busy}
                  onClick={() => void flag()}
                >
                  {busy ? "提交中…" : "标记有问题"}
                </button>
              </div>
              {actionError && (
                <p className="admin-inline-error">{actionError}</p>
              )}
            </article>
          </section>
          <PaginationControls
            page={page}
            pageSize={pageSize}
            total={data.total}
            sort={sort}
            showSort={false}
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
      ) : (
        <div className="admin-empty">没有可审阅的消息。</div>
      )}
    </div>
  );
}
