import { ArrowLeft, ArrowUpRight, ExternalLink } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { notFound } from "next/navigation";
import { BackToTop } from "@/components/back-to-top";
import { PublicShell } from "@/components/public-shell";
import { getEvent } from "@/lib/api";
import { formatPublicTime, publicLabel } from "@/lib/public-labels";

function FactList({ values }: { values: Array<Record<string, unknown>> }) {
  if (values.length === 0) return <p className="event-empty-copy">尚无可以公开展示的结构化事实。</p>;
  return <ol className="event-fact-list">{values.map((value, index) => <li key={`${JSON.stringify(value)}-${index}`}>{String(value.fact ?? value.name ?? value.summary ?? JSON.stringify(value))}</li>)}</ol>;
}

function detailMessageHref(eventId: number, messageId: number) {
  const back = `/events/${eventId}`;
  return `/messages/${messageId}?from=${encodeURIComponent(back)}&fromLabel=${encodeURIComponent("返回事件详情")}`;
}

export default async function EventPage({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ from?: string }> }) {
  const { id } = await params;
  const query = await searchParams;
  const eventId = Number(id);
  if (!Number.isInteger(eventId) || eventId < 1) notFound();
  const event = await getEvent(eventId);
  if (!event) notFound();
  const safeReturn = query.from?.startsWith("/") && !query.from.startsWith("//") ? query.from : "/events";

  return (
    <PublicShell className="event-detail-page">
      <article>
        <header className={`event-detail-hero ${event.best_media_url ? "has-image" : ""}`}>
          {event.best_media_url && <div className="event-detail-image"><Image src={event.best_media_url} alt="" fill sizes="100vw" priority unoptimized referrerPolicy="no-referrer" /></div>}
          <div className="event-detail-overlay" />
          <div className="public-frame event-detail-hero-inner">
            <Link className="message-back" href={safeReturn}><ArrowLeft size={15} /> 返回事件列表</Link>
            <div className="message-detail-kicker"><span>{publicLabel(event.category)}</span><span>{publicLabel(event.event_family)}</span><span>{publicLabel(event.lifecycle_status)}</span></div>
            <h1>{event.title}</h1>
            <p>{event.current_summary}</p>
            <div className="event-detail-updated">最近实质更新 · {formatPublicTime(event.last_material_update_at)}</div>
          </div>
        </header>

        <div className="public-frame event-detail-body">
          <section className="event-metric-grid" aria-label="事件指标">
            <div><span>重要性</span><strong>{Math.round(event.importance_score * 100)}</strong><small>{publicLabel(event.importance_level)}</small></div>
            <div><span>可信度</span><strong>{Math.round(event.credibility_score * 100)}</strong><small>{publicLabel(event.credibility_level)}</small></div>
            <div><span>热度</span><strong>{Math.round(event.heat_score * 100)}</strong><small>{publicLabel(event.heat_level)}</small></div>
            <div><span>覆盖</span><strong>{event.message_count}</strong><small>{event.source_count} 家独立信源</small></div>
          </section>

          <section className="event-development">
            <p className="ln-eyebrow"><i /> Latest Development</p>
            <h2>最新进展</h2>
            <p>{event.latest_development || "当前没有新增的实质进展。"}</p>
          </section>

          <section className="event-detail-section event-facts-section">
            <header><span>01</span><h2>已经知道的事实</h2></header>
            <FactList values={event.key_facts} />
          </section>

          <section className="event-detail-section">
            <header><span>02</span><h2>实质时间线</h2></header>
            {event.timeline.length > 0 ? <div className="event-timeline">{event.timeline.map((node) => (
              <article key={node.mention_id}>
                <time dateTime={node.occurred_at}>{formatPublicTime(node.occurred_at)}</time>
                <i aria-hidden="true" />
                <div><span>{publicLabel(node.relation)} · {node.source_name}</span><h3>{node.title}</h3>{node.note && <p>{node.note}</p>}<Link href={detailMessageHref(event.id, node.message_id)}>阅读对应消息 <ArrowUpRight size={13} /></Link></div>
              </article>
            ))}</div> : <p className="event-empty-copy">尚未形成公开时间线。</p>}
          </section>

          <section className="event-detail-section">
            <header><span>03</span><h2>证据与信源</h2></header>
            {event.evidence.length > 0 ? <div className="event-evidence-list">{event.evidence.map((evidence) => (
              <article key={evidence.mention_id}>
                <div className="ln-card-labels"><span>{publicLabel(evidence.relation)}</span><span>{publicLabel(evidence.source_role)}</span><span>{publicLabel(evidence.materiality)}</span></div>
                <h3>{evidence.source_name}</h3><p>{evidence.evidence_excerpt || "该消息为事件提供背景上下文。"}</p>
                <div><Link href={detailMessageHref(event.id, evidence.message_id)}>查看消息</Link>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">原始来源 <ExternalLink size={12} /></a>}</div>
              </article>
            ))}</div> : <p className="event-empty-copy">尚无公开证据条目。</p>}
          </section>

          {event.related_messages.length > 0 && <section className="event-detail-section related-message-section">
            <header><span>04</span><h2>相关消息</h2></header>
            <div>{event.related_messages.map((message) => <article key={message.message_id}><div><span>{message.source_name}</span><time dateTime={message.published_at ?? undefined}>{formatPublicTime(message.published_at)}</time></div><h3><Link href={detailMessageHref(event.id, message.message_id)}>{message.title}</Link></h3><p>{message.summary}</p></article>)}</div>
          </section>}
        </div>
      </article>
      <BackToTop />
    </PublicShell>
  );
}
