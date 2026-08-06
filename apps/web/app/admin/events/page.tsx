"use client";

import Link from "next/link";
import { Search } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { adminApi } from "@/lib/api";
import type { EventDetail, EventPage, EventSummary } from "@/lib/types";
import { EventTimeline } from "@/components/admin/EventTimeline";
import { MultiMembershipView } from "@/components/admin/MultiMembershipView";
import { PaginationControls } from "@/components/admin/PaginationControls";
import { score } from "@/components/admin/admin-utils";

type View = "list" | "timeline" | "multi";
const timelineTypes = new Set(["transfer_saga", "patch_cycle", "release_saga", "dev_preview", "incident", "qualification_saga"]);

export default function EventsPage() {
  const [data, setData] = useState<EventPage>({ items: [], total: 0 });
  const events = data.items;
  const [details, setDetails] = useState<EventDetail[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [view, setView] = useState<View>("list");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [sort, setSort] = useState<"asc" | "desc">("desc");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
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
    try {
      const params = new URLSearchParams({
        limit: String(pageSize),
        offset: String((page - 1) * pageSize),
        sort,
      });
      if (query) params.set("search", query);
      const rows = await adminApi<EventPage>(`/events/page?${params}`);
      setData(rows);
      setDetails([]);
      setSelectedId(
        rows.items.find((event) => timelineTypes.has(event.event_type))?.id ??
          null,
      );
    } catch (value) {
      setError(value instanceof Error ? value.message : "事件加载失败");
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, query, sort]);
  useEffect(() => {
    void load();
  }, [load]);
  const loadDetail = useCallback(
    async (id: number) => {
      const existing = details.find((event) => event.id === id);
      if (existing) return existing;
      setDetailLoading(true);
      try {
        const detail = await adminApi<EventDetail>(`/events/${id}`);
        setDetails((rows) => [...rows.filter((row) => row.id !== id), detail]);
        return detail;
      } finally {
        setDetailLoading(false);
      }
    },
    [details],
  );
  useEffect(() => {
    if (view === "timeline" && selectedId) void loadDetail(selectedId);
    if (view === "multi" && !detailLoading) {
      const missing = events.filter(
        (event) => !details.some((detail) => detail.id === event.id),
      );
      if (missing.length) {
        setDetailLoading(true);
        void Promise.all(
          missing.map((event) => adminApi<EventDetail>(`/events/${event.id}`)),
        )
          .then((rows) => setDetails(rows))
          .catch((value) =>
            setError(
              value instanceof Error ? value.message : "多归属数据加载失败",
            ),
          )
          .finally(() => setDetailLoading(false));
      }
    }
  }, [detailLoading, details, events, loadDetail, selectedId, view]);
  const grouped = useMemo(() => {
    const map = new Map<string, EventSummary[]>();
    for (const event of events)
      map.set(event.event_type, [...(map.get(event.event_type) ?? []), event]);
    return map;
  }, [events]);
  const selected = details.find((event) => event.id === selectedId);
  const remove = async (eventId: number, itemId: number) => {
    if (!window.confirm("确认解除这条消息与事件的关联？该操作会保留修订记录。"))
      return;
    try {
      await adminApi(`/events/${eventId}/messages/${itemId}`, {
        method: "DELETE",
      });
      const rows = await Promise.all(
        events.map((event) => adminApi<EventDetail>(`/events/${event.id}`)),
      );
      setDetails(rows);
    } catch (value) {
      setError(value instanceof Error ? value.message : "解除关联失败");
    }
  };
  const pagination = (
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
  );
  return (
    <div className="admin-page">
      <header className="admin-page-head">
        <div>
          <span className="admin-eyebrow">EVENT GRAPH</span>
          <h1>事件管理</h1>
          <p>共 {data.total} 个事件，默认按最新进展时间倒序展示。</p>
        </div>
        <div className="admin-view-toggle">
          <button
            className={view === "list" ? "active" : ""}
            onClick={() => setView("list")}
          >
            列表
          </button>
          <button
            className={view === "timeline" ? "active" : ""}
            onClick={() => setView("timeline")}
          >
            时间线
          </button>
          <button
            className={view === "multi" ? "active" : ""}
            onClick={() => setView("multi")}
          >
            多归属
          </button>
        </div>
      </header>
      <section className="admin-filters admin-event-filters">
        <label className="admin-search">
          <span>搜索</span>
          <div>
            <Search size={15} />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="事件 ID、标题或摘要"
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
      {loading ? (
        <div className="admin-skeleton admin-skeleton-table" />
      ) : view === "list" ? (
        <>
          <div className="admin-event-groups">
            {[...grouped].map(([type, rows]) => (
              <details open key={type}>
                <summary>
                  <span>{type}</span>
                  <b>{rows.length}</b>
                </summary>
                <div>
                  {rows.map((event) => (
                    <Link
                      href={`/admin/events/${event.id}`}
                      className="admin-event-row"
                      key={event.id}
                    >
                      <code>事件 ID #{event.id}</code>
                      <strong>{event.title}</strong>
                      <span className="admin-badge">
                        {event.lifecycle_status}
                      </span>
                      <span className="admin-badge subtle">
                        {event.credibility_status}
                      </span>
                      <span>{score(event.credibility_score)} cred</span>
                      <span>{event.message_count} 消息</span>
                      <span>{event.independent_source_count} 信源</span>
                      <b>详情 →</b>
                    </Link>
                  ))}
                </div>
              </details>
            ))}
          </div>
          {pagination}
        </>
      ) : view === "timeline" ? (
        <>
          <div className="admin-subfilters">
            <label>
              时间线事件
              <select
                value={selectedId ?? ""}
                onChange={(event) => setSelectedId(Number(event.target.value))}
              >
                {events
                  .filter((event) => timelineTypes.has(event.event_type))
                  .map((event) => (
                    <option value={event.id} key={event.id}>
                      #{event.id} · {event.event_type} · {event.title}
                    </option>
                  ))}
              </select>
            </label>
          </div>
          {detailLoading && !selected ? (
            <div className="admin-skeleton admin-skeleton-table" />
          ) : selected ? (
            <EventTimeline event={selected} />
          ) : (
            <div className="admin-empty">当前页没有可展示的时间线事件。</div>
          )}
          {pagination}
        </>
      ) : (
        <>
          {detailLoading ? (
            <div className="admin-skeleton admin-skeleton-table" />
          ) : (
            <MultiMembershipView
              events={details}
              onRemove={(eventId, itemId) => void remove(eventId, itemId)}
            />
          )}
          {pagination}
        </>
      )}
    </div>
  );
}
