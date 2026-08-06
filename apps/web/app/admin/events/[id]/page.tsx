import Link from "next/link";
import { notFound } from "next/navigation";
import { adminApi } from "@/lib/api";
import type { EventDetail } from "@/lib/types";
import { EventTimeline } from "@/components/admin/EventTimeline";
import { EventActions } from "@/components/admin/EventActions";
import { score } from "@/components/admin/admin-utils";

export default async function EventDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const event = await adminApi<EventDetail>(`/events/${id}`).catch(() => null);
  if (!event) notFound();
  const supporting = event.messages.filter((message) => message.evidence_stance === "supports");
  const contradicting = event.messages.filter((message) => message.evidence_stance === "contradicts");
  return <div className="admin-page admin-detail-page">
    <Link className="admin-back" href="/admin/events">← 返回事件管理</Link>
    <header className="admin-detail-head"><div className="admin-badge-row"><span className="admin-badge">{event.event_type}</span><span className="admin-badge subtle">{event.lifecycle_status}</span><span className="admin-badge success">{event.credibility_status} · {score(event.credibility_score)}</span></div><h1>{event.title}</h1><code>{event.aggregation_key ?? event.event_key ?? "无聚合键"}</code><p>{event.summary}</p>{event.latest_development && <div className="admin-latest"><strong>最新进展</strong>{event.latest_development}</div>}</header>
    <section className="admin-detail-section"><EventTimeline event={event} /></section>
    <section className="admin-detail-section"><h2>成员消息</h2><div className="admin-table-scroll"><table className="admin-table"><thead><tr><th>消息</th><th>角色</th><th>立场</th><th>来源</th><th>证据配置</th></tr></thead><tbody>{event.messages.map((message) => <tr key={`${message.normalized_item_id}-${message.membership_role}`}><td><Link href={`/admin/messages/${message.normalized_item_id}`}>{message.title}</Link></td><td><span className="admin-badge">{message.membership_role}</span></td><td>{message.evidence_stance}</td><td>{message.source_name}</td><td>{message.is_official_evidence ? "官方直接证据" : `可靠性快照 ${score(message.source_reliability_snapshot)}`}</td></tr>)}</tbody></table></div></section>
    <section className="admin-detail-section"><h2>事件可信度</h2><div className="admin-evidence-columns"><article><h3>正向信源 · {event.supporting_source_count}</h3>{supporting.map((message) => <p key={message.normalized_item_id}><strong>{message.source_name}</strong><span>{score(message.source_reliability_snapshot)}</span></p>)}</article><article><h3>负向信源 · {event.contradicting_source_count}</h3>{contradicting.map((message) => <p key={message.normalized_item_id}><strong>{message.source_name}</strong><span>{score(message.source_reliability_snapshot)}</span></p>)}</article><code>最高基础分 + 独立支持信源加成（非官方封顶 0.9）= {score(event.credibility_score)}</code></div></section>
    <section className="admin-detail-section"><h2>修订历史</h2><div className="admin-revisions">{[...event.revisions].reverse().map((revision) => <details key={revision.id}><summary><b>v{revision.revision}</b><span>{revision.change_note}</span><time>{new Date(revision.created_at).toLocaleString("zh-CN")}</time></summary><p>{revision.summary}</p><pre>{JSON.stringify(revision.evidence_snapshot, null, 2)}</pre></details>)}</div></section>
    <section className="admin-detail-section"><h2>事件操作</h2><EventActions eventId={event.id} lifecycle={event.lifecycle_status} summary={event.summary} /></section>
  </div>;
}
