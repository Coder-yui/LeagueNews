import { ArrowLeft, ExternalLink, GitBranch } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";
import { getEvent } from "@/lib/api";

export default async function EventPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
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
        <div className="live-state"><span /> Revision {event.current_revision}</div>
      </header>
      <Link className="message-back" href="/events">
        <ArrowLeft size={15} /> 返回事件列表
      </Link>
      <article className="event-detail">
        <header className="event-detail-head">
          <div className="event-topline">
            <span>{event.category}</span>
            <span>{event.event_key ?? `EVENT:${event.id}`}</span>
            <span>{event.status}</span>
          </div>
          <h1>{event.title}</h1>
          <p>{event.summary}</p>
          <div className="public-event-meta">
            <span><GitBranch size={13} /> Revision {event.current_revision}</span>
            <span>{event.message_count} 条证据消息</span>
            {event.first_published_at && <time>首次 {new Date(event.first_published_at).toLocaleString("zh-CN")}</time>}
            {event.last_published_at && <time>最近 {new Date(event.last_published_at).toLocaleString("zh-CN")}</time>}
          </div>
        </header>
        <section className="event-timeline">
          <div className="section-heading"><div><span className="kicker">SOURCE TIMELINE</span><h2>消息时间线</h2></div></div>
          {event.messages.map((message) => (
            <article className="timeline-item" key={message.normalized_item_id}>
              <time>{new Date(message.source_published_at ?? message.added_at).toLocaleString("zh-CN")}</time>
              <div>
                <span>{message.source_name}</span>
                <h3><Link href={`/messages/${message.normalized_item_id}`}>{message.title}</Link></h3>
                <p>{message.summary}</p>
                <div className="timeline-links">
                  <Link href={`/messages/${message.normalized_item_id}`}>查看审核消息</Link>
                  {message.source_url && <a href={message.source_url} target="_blank" rel="noreferrer">查看原文 <ExternalLink size={12} /></a>}
                </div>
              </div>
            </article>
          ))}
        </section>
        <section className="revision-history">
          <div className="section-heading"><div><span className="kicker">AUDIT HISTORY</span><h2>Revision 历史</h2></div></div>
          {event.revisions.map((revision) => (
            <article key={revision.id}>
              <strong>Revision {revision.revision}</strong>
              <time>{new Date(revision.created_at).toLocaleString("zh-CN")}</time>
              <h3>{revision.title}</h3>
              <p>{revision.change_note}</p>
            </article>
          ))}
        </section>
      </article>
      <footer><span>LoL Daily Intel · Event audit</span><span>Human-approved revisions only.</span></footer>
    </main>
  );
}
