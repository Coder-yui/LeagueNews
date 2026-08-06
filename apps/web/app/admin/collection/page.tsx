"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "@/lib/api";
import type { CollectionSchedule, ConnectorRunPage, Source } from "@/lib/types";
import { SourceStatusRow } from "@/components/admin/SourceStatusRow";
import { CollectionScheduleEditor } from "@/components/admin/CollectionScheduleEditor";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { relativeTime } from "@/components/admin/admin-utils";

export default function CollectionPage() {
  const [tab, setTab] = useState<"sources" | "logs">("sources");
  const [sources, setSources] = useState<Source[]>([]);
  const [schedules, setSchedules] = useState<CollectionSchedule[]>([]);
  const [runs, setRuns] = useState<ConnectorRunPage>({ items: [], total: 0 });
  const [errors, setErrors] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [sourceFilter, setSourceFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState<
    "all" | "completed" | "failed"
  >("failed");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sort, setSort] = useState<"asc" | "desc">("desc");
  const [sourcePageNumber, setSourcePageNumber] = useState(1);
  const loadSources = useCallback(async () => {
    const [sourceRows, scheduleRows] = await Promise.all([
      adminApi<Source[]>("/sources"),
      adminApi<CollectionSchedule[]>("/collection-schedules"),
    ]);
    setSources(sourceRows);
    setSchedules(scheduleRows);
  }, []);
  const loadRuns = useCallback(async () => {
    const params = new URLSearchParams({
      limit: String(pageSize),
      offset: String((page - 1) * pageSize),
      sort,
    });
    if (sourceFilter !== "all") params.set("source_id", sourceFilter);
    if (statusFilter !== "all") params.set("status", statusFilter);
    setRuns(
      await adminApi<ConnectorRunPage>(`/connectors/runs/page?${params}`),
    );
  }, [page, pageSize, sort, sourceFilter, statusFilter]);
  const load = useCallback(async () => {
    setLoading(true);
    const nextErrors: string[] = [];
    const results = await Promise.allSettled([loadSources(), loadRuns()]);
    if (results[0].status === "rejected")
      nextErrors.push("来源与采集计划加载失败");
    if (results[1].status === "rejected") nextErrors.push("采集日志加载失败");
    setErrors(nextErrors);
    setLoading(false);
  }, [loadRuns, loadSources]);
  useEffect(() => {
    void load();
  }, [load]);
  const scheduleBySource = useMemo(
    () => new Map(schedules.map((schedule) => [schedule.source_id, schedule])),
    [schedules],
  );
  const sourceNameById = useMemo(
    () => new Map(sources.map((source) => [source.id, source.name])),
    [sources],
  );
  const editingSource = sources.find((source) => source.id === editingId);
  const sourcePageSize = 25;
  const sourcePageCount = Math.max(
    1,
    Math.ceil(sources.length / sourcePageSize),
  );
  const sourcePage = sources.slice(
    (sourcePageNumber - 1) * sourcePageSize,
    sourcePageNumber * sourcePageSize,
  );

  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">INGESTION</span>
          <h1>数据采集</h1>
          <p>在来源配置和完整采集日志之间切换。</p>
        </div>
      </header>
      <div className="admin-queue-tabs">
        <button
          className={tab === "sources" ? "active" : ""}
          onClick={() => setTab("sources")}
        >
          来源 <b>{sources.length}</b>
        </button>
        <button
          className={tab === "logs" ? "active" : ""}
          onClick={() => setTab("logs")}
        >
          采集日志 <b>{runs.total}</b>
        </button>
      </div>
      {errors.map((error) => (
        <div className="admin-error-state" key={error}>
          <span>{error}</span>
          <button onClick={() => void load()}>重试</button>
        </div>
      ))}
      {tab === "sources" ? (
        <section className="admin-panel no-margin">
          <div className="admin-section-title">
            <h2>来源</h2>
            <span>显示自动采集计划与最近成功时间</span>
          </div>
          {loading ? (
            <div className="admin-skeleton admin-skeleton-table" />
          ) : (
            <>
              <div className="admin-table-scroll">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Source ID</th>
                      <th>来源名</th>
                      <th>类型</th>
                      <th>计划状态</th>
                      <th>采集频率</th>
                      <th>上次成功</th>
                      <th>操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sourcePage.map((source) => (
                      <SourceStatusRow
                        key={source.id}
                        source={source}
                        schedule={scheduleBySource.get(source.id)}
                        onEdit={() => setEditingId(source.id)}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="admin-pagination">
                <span>共 {sources.length} 个来源</span>
                <div>
                  <button
                    type="button"
                    disabled={sourcePageNumber <= 1}
                    onClick={() => setSourcePageNumber((value) => value - 1)}
                  >
                    上一页
                  </button>
                  <b>
                    {sourcePageNumber} / {sourcePageCount}
                  </b>
                  <button
                    type="button"
                    disabled={sourcePageNumber >= sourcePageCount}
                    onClick={() => setSourcePageNumber((value) => value + 1)}
                  >
                    下一页
                  </button>
                </div>
              </div>
            </>
          )}
        </section>
      ) : (
        <section className="admin-panel no-margin">
          <div className="admin-log-filters">
            <label>
              来源
              <select
                value={sourceFilter}
                onChange={(event) => {
                  setSourceFilter(event.target.value);
                  setPage(1);
                }}
              >
                <option value="all">全部</option>
                {sources.map((source) => (
                  <option value={source.id} key={source.id}>
                    {source.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              状态
              <select
                value={statusFilter}
                onChange={(event) => {
                  setStatusFilter(
                    event.target.value as "all" | "completed" | "failed",
                  );
                  setPage(1);
                }}
              >
                <option value="all">全部</option>
                <option value="completed">成功</option>
                <option value="failed">失败</option>
              </select>
            </label>
          </div>
          <div className="admin-log-list">
            {runs.items.map((run) => {
              const detail =
                run.status === "completed"
                  ? `发现 ${run.discovered_count} · 新增 ${run.created_count} · 修订 ${run.revised_count}`
                  : (run.error_message ?? run.status);
              return (
                <article key={run.id} className={run.status}>
                  <time
                    title={new Date(run.started_at).toLocaleString("zh-CN")}
                  >
                    {relativeTime(run.started_at)}
                  </time>
                  <span className={`admin-status-dot ${run.status}`} />
                  <strong>
                    {sourceNameById.get(run.source_id) ??
                      `source #${run.source_id}`}
                  </strong>
                  <span>{run.connector_type}</span>
                  <b title={detail}>
                    {detail.length > 140 ? `${detail.slice(0, 140)}…` : detail}
                  </b>
                </article>
              );
            })}
            {!runs.items.length && (
              <div className="admin-empty">没有符合条件的采集日志。</div>
            )}
          </div>
          <PaginationControls
            page={page}
            pageSize={pageSize}
            total={runs.total}
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
        </section>
      )}
      {editingSource && (
        <CollectionScheduleEditor
          source={editingSource}
          schedule={scheduleBySource.get(editingSource.id)}
          onClose={() => setEditingId(null)}
          onSaved={() => {
            setEditingId(null);
            void loadSources();
          }}
        />
      )}
    </div>
  );
}
