import Image from "next/image";
import Link from "next/link";
import { Activity, ArrowUpRight } from "lucide-react";
import { getEventsPage } from "@/lib/api";

export default async function EventsPage() {
  const page = await getEventsPage();
  return (
    <main>
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
        <div className="live-state"><span /> Event projection</div>
      </header>

      <section className="hero event-hero">
        <div className="eyebrow"><Activity size={15} /> CURRENT EVENTS</div>
        <h1>正在发生的事，<br /><em>而不只是消息。</em></h1>
        <p>事件摘要只随实质进展更新；重要性、可信度与热度分别计算。</p>
      </section>

      <section className="messages-section">
        <div className="section-heading">
          <div><span className="kicker">EVENT STREAM</span><h2>事件列表</h2></div>
          <span>{page.total} 个当前事件</span>
        </div>
        {page.items.length === 0 ? (
          <div className="message-empty">目前还没有完成聚合的事件。</div>
        ) : (
          <div className="message-list">
            {page.items.map((event, index) => (
              <article className="message-card event-card" key={event.id}>
                <div className="message-card-index">{String(index + 1).padStart(2, "0")}</div>
                <div className="message-card-copy">
                  <div className="message-card-meta">
                    <span>#{event.id} · {event.event_family}</span>
                    <span>{event.lifecycle_status}</span>
                    {event.primary_source && <span>主要来源 {event.primary_source.source_name}</span>}
                  </div>
                  <Link className="message-card-title" href={`/events/${event.id}`}>
                    <h3>{event.title}</h3>
                  </Link>
                  <p>{event.current_summary}</p>
                  <div className="message-card-footer">
                    <span className="importance-badge">重要性 {Math.round(event.importance_score * 100)}</span>
                    <span className="topic-badge">可信度 {event.credibility_level}</span>
                    <span className="topic-badge">热度 {Math.round(event.heat_score * 100)}</span>
                    <span className="entity">24h {event.message_count_24h} 条 / {event.unique_sources_24h} 来源</span>
                  </div>
                  <Link className="message-card-link" href={`/events/${event.id}`}>
                    查看事件详情 <ArrowUpRight size={14} />
                  </Link>
                </div>
                {event.best_media_url && (
                  <Link className="message-card-image" href={`/events/${event.id}`} tabIndex={-1}>
                    <Image
                      src={event.best_media_url}
                      alt={event.title}
                      width={520}
                      height={360}
                      sizes="(max-width: 760px) 100vw, 320px"
                      unoptimized
                    />
                  </Link>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
      <footer><span>LoL Daily Intel · Current events</span><span>Importance ≠ credibility ≠ heat</span></footer>
    </main>
  );
}
