import { ArrowLeft, ExternalLink } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getEvent } from "@/lib/api";

function FactList({ values, empty }: { values: Array<Record<string, unknown>>; empty: string }) {
  if (values.length === 0) return <p className="event-empty-copy">{empty}</p>;
  return (
    <ul className="event-fact-list">
      {values.map((value, index) => (
        <li key={`${JSON.stringify(value)}-${index}`}>{String(value.fact ?? value.name ?? JSON.stringify(value))}</li>
      ))}
    </ul>
  );
}

export default async function EventPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const eventId = Number(id);
  if (!Number.isInteger(eventId) || eventId < 1) notFound();
  const event = await getEvent(eventId);
  if (!event) notFound();

  return (
    <main className="message-page">
      <header className="site-header">
        <Link className="brand" href="/">
          <span className="brand-mark">LD</span>
          <span>LoL Daily Intel</span>
        </Link>
        <nav aria-label="主要导航">
          <Link href="/">消息</Link>
          <Link className="active" href="/events">事件</Link>
          <Link href="/admin">处理台</Link>
        </nav>
        <div className="live-state"><span /> Current state</div>
      </header>
      <Link className="message-back" href="/events"><ArrowLeft size={15} /> 返回事件列表</Link>

      <article className="message-detail event-detail">
        <header className="message-detail-head">
          <div className="message-detail-kicker">
            <span>#{event.id}</span><span>{event.event_family}</span><span>{event.lifecycle_status}</span>
          </div>
          <h1>{event.title}</h1>
          <p>{event.current_summary}</p>
          <div className="event-metric-grid">
            <div><span>重要性</span><strong>{Math.round(event.importance_score * 100)}</strong><small>{event.importance_level}</small></div>
            <div><span>可信度</span><strong>{Math.round(event.credibility_score * 100)}</strong><small>{event.credibility_level}</small></div>
            <div><span>热度</span><strong>{Math.round(event.heat_score * 100)}</strong><small>{event.heat_level}</small></div>
            <div><span>事件覆盖</span><strong>{event.message_count}</strong><small>{event.source_count} 家信源 · 消息去重</small></div>
          </div>
        </header>

        <section className="event-section"><span className="reader-label">LATEST DEVELOPMENT</span><p>{event.latest_development || "暂无新增进展。"}</p></section>
        <div className="event-two-columns">
          <section className="event-section"><h2>关键事实</h2><FactList values={event.key_facts} empty="尚无结构化关键事实。" /></section>
        </div>

        <section className="event-section">
          <h2>实质时间线</h2>
          <div className="event-timeline">
            {event.timeline.map((node) => (
              <article key={node.mention_id}>
                <time dateTime={node.occurred_at}>{new Date(node.occurred_at).toLocaleString("zh-CN")}</time>
                <div><strong>{node.relation} · {node.source_name}</strong><p>{node.note || node.title}</p><Link href={`/messages/${node.message_id}`}>查看消息（修订 {node.message_revision}）</Link></div>
              </article>
            ))}
          </div>
        </section>

        <section className="event-section">
          <h2>证据与相关消息</h2>
          <div className="event-evidence-list">
            {event.evidence.map((evidence) => (
              <article key={evidence.mention_id}>
                <span>{evidence.relation} · {evidence.source_role} · {evidence.materiality}</span>
                <p>{evidence.evidence_excerpt || "该消息仅提供上下文。"}</p>
                <div><Link href={`/messages/${evidence.message_id}`}>消息 #{evidence.message_id} · 修订 {evidence.message_revision}</Link>{evidence.source_url && <a href={evidence.source_url} target="_blank" rel="noreferrer">原始来源 <ExternalLink size={12} /></a>}</div>
              </article>
            ))}
          </div>
        </section>
      </article>
      <footer><span>LoL Daily Intel · Event #{event.id}</span><span>{event.message_count_total} 条相关消息</span></footer>
    </main>
  );
}
