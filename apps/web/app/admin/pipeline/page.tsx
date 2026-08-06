"use client";

import { RefreshCw, Search } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { adminApi } from "@/lib/api";
import type {
  PipelineCorrection,
  RawAdminItem,
  RawAdminPage,
} from "@/lib/types";
import { PipelineStageBar } from "@/components/admin/PipelineStageBar";
import { ExpandableRow } from "@/components/admin/ExpandableRow";
import { ItemDetailCard } from "@/components/admin/ItemDetailCard";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { inferStages, relativeTime } from "@/components/admin/admin-utils";

type FilterStatus = "all" | "failed" | "processing" | "completed";

function rowStatus(
  item: RawAdminItem,
): Exclude<FilterStatus, "all"> | "pending" {
  const run = item.processing_runs[0];
  if (
    run?.status === "failed" ||
    item.current_pipeline_job_status === "failed" ||
    item.processing_status === "failed"
  )
    return "failed";
  if (
    run?.status === "running" ||
    run?.status === "awaiting_review" ||
    ["running", "queued"].includes(item.current_pipeline_job_status ?? "")
  )
    return "processing";
  if (
    item.processing_status === "analyzed" ||
    item.normalized_item_id ||
    item.current_pipeline_job_status === "completed"
  )
    return "completed";
  return "pending";
}

export default function PipelinePage() {
  const router = useRouter();
  const [data, setData] = useState<RawAdminPage>({
    items: [],
    total: 0,
    total_items: 0,
    status_counts: { all: 0, failed: 0, processing: 0, completed: 0 },
    source_options: [],
    content_type_options: [],
  });
  const [corrections, setCorrections] = useState<PipelineCorrection[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<number | null>(null);
  const [actionError, setActionError] = useState<Record<number, string>>({});
  const [sourceId, setSourceId] = useState("all");
  const [type, setType] = useState("all");
  const [status, setStatus] = useState<FilterStatus>("failed");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<number | null>(null);
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
    const params = new URLSearchParams({
      process_status: status,
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
      sort,
    });
    if (sourceId !== "all") params.set("source_id", sourceId);
    if (type !== "all") params.set("content_type", type);
    if (query) params.set("search", query);
    try {
      const [pageData, correctionRows] = await Promise.all([
        adminApi<RawAdminPage>(`/raw-items/admin-page?${params}`),
        adminApi<PipelineCorrection[]>("/pipeline/corrections"),
      ]);
      setData(pageData);
      setCorrections(correctionRows);
    } catch (value) {
      setError(value instanceof Error ? value.message : "流水线数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, query, sort, sourceId, status, type]);
  useEffect(() => {
    void load();
  }, [load]);

  const act = async (item: RawAdminItem) => {
    const itemStatus = rowStatus(item);
    if (itemStatus === "completed") {
      setExpanded((value) => (value === item.id ? null : item.id));
      return;
    }
    if (item.processing_runs[0]?.status === "awaiting_review") {
      router.push("/admin/reviews");
      return;
    }
    setBusy(item.id);
    setActionError((value) => ({ ...value, [item.id]: "" }));
    try {
      if (itemStatus === "failed" && item.processing_runs[0])
        await adminApi(`/workflows/runs/${item.processing_runs[0].id}/retry`, {
          method: "POST",
          body: "{}",
        });
      else
        await adminApi(`/raw-items/${item.id}/process`, {
          method: "POST",
          body: "{}",
        });
      await load();
    } catch (value) {
      setActionError((current) => ({
        ...current,
        [item.id]: value instanceof Error ? value.message : "操作失败",
      }));
    } finally {
      setBusy(null);
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
          <span className="admin-eyebrow">PIPELINE CONTROL</span>
          <h1>流水线监控</h1>
          <p>
            默认聚焦处理失败的消息；全部 {data.total_items} 条 RawItem
            均可分页查看。
          </p>
        </div>
        <button
          type="button"
          className="admin-primary-button"
          onClick={() => void load()}
          disabled={loading}
        >
          <RefreshCw size={15} className={loading ? "spin" : ""} />
          刷新
        </button>
      </header>
      <section className="admin-metrics">
        <div>
          <span>全部 RawItem</span>
          <strong>{data.total_items}</strong>
        </div>
        <div className={data.status_counts.failed ? "danger" : ""}>
          <span>处理失败</span>
          <strong>{data.status_counts.failed}</strong>
        </div>
        <div>
          <span>处理中</span>
          <strong>{data.status_counts.processing}</strong>
        </div>
        <div>
          <span>处理完成</span>
          <strong>{data.status_counts.completed}</strong>
        </div>
        <div>
          <span>重跑记录</span>
          <strong>{corrections.length}</strong>
        </div>
      </section>
      <section className="admin-filters">
        <label>
          处理状态
          <select
            value={status}
            onChange={(event) =>
              resetPage(() => setStatus(event.target.value as FilterStatus))
            }
          >
            <option value="all">全部</option>
            <option value="failed">处理失败</option>
            <option value="processing">处理中</option>
            <option value="completed">处理完成</option>
          </select>
        </label>
        <label>
          来源
          <select
            value={sourceId}
            onChange={(event) =>
              resetPage(() => setSourceId(event.target.value))
            }
          >
            <option value="all">全部</option>
            {data.source_options.map((source) => (
              <option value={source.id} key={source.id}>
                {source.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          内容类型
          <select
            value={type}
            onChange={(event) => resetPage(() => setType(event.target.value))}
          >
            <option value="all">全部</option>
            {data.content_type_options.map((value) => (
              <option value={value} key={value}>
                {value}
              </option>
            ))}
          </select>
        </label>
        <label className="admin-search">
          <span>搜索</span>
          <div>
            <Search size={15} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="标题或摘要"
            />
          </div>
        </label>
      </section>
      {error && (
        <div className="admin-error-state">
          <span>{error}</span>
          <button type="button" onClick={() => void load()}>
            重试
          </button>
        </div>
      )}
      {loading && !data.items.length ? (
        <div className="admin-skeleton admin-skeleton-table" />
      ) : (
        <>
          <div className="admin-table-scroll">
            <table className="admin-table admin-pipeline-table">
              <thead>
                <tr>
                  <th>消息 ID</th>
                  <th>来源</th>
                  <th>标题预览</th>
                  <th>发布时间</th>
                  <th>处理进度</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((item) => {
                  const itemStatus = rowStatus(item);
                  const label =
                    itemStatus === "completed"
                      ? "查看"
                      : itemStatus === "failed"
                        ? "重试"
                        : item.processing_runs[0]?.status === "awaiting_review"
                          ? "审核"
                          : itemStatus === "processing"
                            ? "处理中"
                            : "处理";
                  return (
                    <ExpandableRow
                      key={item.id}
                      open={expanded === item.id}
                      colSpan={6}
                      detail={
                        <ItemDetailCard
                          item={item}
                          onChanged={() => void load()}
                        />
                      }
                      row={
                        <tr
                          className="admin-clickable-row"
                          onClick={() =>
                            setExpanded((value) =>
                              value === item.id ? null : item.id,
                            )
                          }
                        >
                          <td className="admin-number">#{item.id}</td>
                          <td>
                            <span className="admin-source-badge">
                              {item.source_connector_type}
                            </span>
                            <small>{item.source_name}</small>
                          </td>
                          <td>
                            <strong>
                              {(item.display_title ?? "无标题").slice(0, 40)}
                            </strong>
                            <small>{item.content_type ?? "未分类"}</small>
                          </td>
                          <td
                            title={new Date(
                              item.published_at ?? item.ingested_at,
                            ).toLocaleString("zh-CN")}
                          >
                            {relativeTime(
                              item.published_at ?? item.ingested_at,
                            )}
                          </td>
                          <td>
                            <PipelineStageBar stages={inferStages(item)} />
                          </td>
                          <td>
                            <button
                              className={`admin-table-button ${itemStatus === "failed" ? "danger" : ""}`}
                              type="button"
                              disabled={
                                busy === item.id || itemStatus === "processing"
                              }
                              onClick={(event) => {
                                event.stopPropagation();
                                void act(item);
                              }}
                            >
                              {busy === item.id ? "提交中…" : label}
                            </button>
                            {actionError[item.id] && (
                              <small className="admin-inline-error">
                                {actionError[item.id]}
                              </small>
                            )}
                          </td>
                        </tr>
                      }
                    />
                  );
                })}
              </tbody>
            </table>
            {!data.items.length && (
              <div className="admin-empty">没有符合当前筛选条件的消息。</div>
            )}
          </div>
          <PaginationControls
            page={page}
            pageSize={pageSize}
            total={data.total}
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
    </div>
  );
}
