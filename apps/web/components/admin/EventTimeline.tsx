"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { adminApi } from "@/lib/api";
import type { EventDetail, EventMessage } from "@/lib/types";
import { relativeTime, score } from "./admin-utils";

function stanceSymbol(stance: string): string { return stance === "supports" ? "+" : stance === "contradicts" ? "−" : "○"; }

export function EventTimeline({ event, onExpire }: { event: EventDetail; onExpire?: () => void }) {
  const router = useRouter(); const [expiring, setExpiring] = useState(false);
  const stale = event.lifecycle_status === "unconfirmed" && event.last_published_at && Date.now() - new Date(event.last_published_at).getTime() > 3 * 86_400_000;
  return (
    <section className="admin-event-timeline">
      <header><div><h2>{event.title}</h2><code>{event.aggregation_key ?? event.event_key ?? "无聚合键"}</code></div><span>{event.event_type}</span></header>
      <ol>
        {event.messages.map((message: EventMessage) => <li key={`${message.normalized_item_id}-${message.membership_role ?? "primary"}`} className={`${message.evidence_stance} ${message.update_kind}`}><span className="admin-timeline-dot">{stanceSymbol(message.evidence_stance)}</span><time title={new Date(message.source_published_at ?? message.added_at).toLocaleString("zh-CN")}>{relativeTime(message.source_published_at ?? message.added_at)}</time><article><div><span className="admin-badge">{message.membership_role ?? message.relation_type}</span><span className="admin-badge subtle">{message.update_kind}</span><strong>{message.source_name}</strong>{message.is_official_evidence && <b>官方证据</b>}</div><h3>{message.timeline_note}</h3><Link href={`/admin/messages/${message.normalized_item_id}`}>{message.title}</Link><p>{message.summary}</p>{message.source_url && <a href={message.source_url} target="_blank" rel="noreferrer">原文证据</a>}</article></li>)}
      </ol>
      {stale && <div className="admin-awaiting">等待确认 · {relativeTime(event.last_published_at)}<button type="button" disabled={expiring} onClick={() => { if (onExpire) { onExpire(); return; } setExpiring(true); void adminApi(`/events/${event.id}`, { method: "PATCH", body: JSON.stringify({ lifecycle_status: "expired_unconfirmed", change_note: "管理台标记传闻过期" }) }).then(() => router.refresh()).finally(() => setExpiring(false)); }}>{expiring ? "标记中…" : "标记过期"}</button></div>}
      <footer><span>当前状态 <b>{event.lifecycle_status}</b></span><span>事件可信度 <b>{score(event.credibility_score)}</b></span><span>支持/反对 <b>{event.supporting_source_count}/{event.contradicting_source_count}</b></span></footer>
    </section>
  );
}
