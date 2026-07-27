import { ArrowUpRight } from "lucide-react";
import Link from "next/link";
import { getEvents } from "@/lib/api";
import {
  credibilityLabel,
  eventTypeLabel,
  importanceLevel,
  lifecycleLabel,
} from "@/lib/event-labels";

export default async function EventsPage() {
  const events = await getEvents();
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
        <div className="live-state"><span /> Reviewed</div>
      </header>
      <section className="event-page-head">
        <span className="kicker">EVOLVING STORIES</span>
        <h1>持续演化的事件</h1>
        <p>把多条经人工审核的消息组织成可追溯时间线，同时保留每条消息的独立证据。</p>
      </section>
      <section className="public-event-list">
        {!events.length && <div className="message-empty">目前还没有完成审核的事件。</div>}
        {events.map((event) => (
          <article className="public-event-card" key={event.id}>
            <div className="event-topline">
              <span>{event.category}</span>
              <span>{eventTypeLabel(event.event_type)}</span>
              <span className={`event-state ${event.lifecycle_status}`}>
                {lifecycleLabel(event.lifecycle_status)}
              </span>
            </div>
            <h2><Link href={`/events/${event.id}`}>{event.title}</Link></h2>
            <p>{event.summary}</p>
            {event.latest_development && (
              <p className="event-latest">
                <strong>最新进展</strong>{event.latest_development}
              </p>
            )}
            <div className="public-event-meta">
              <span className={`importance-badge ${importanceLevel(event.importance_score)}`}>
                重要性 {Math.round(event.importance_score * 100)}
              </span>
              <span className={`credibility-badge ${event.credibility_status}`}>
                {credibilityLabel(event.credibility_status)}
                {event.credibility_status !== "officially_refuted"
                  && ` ${Math.round(event.credibility_score * 100)}`}
              </span>
              <span>{event.independent_source_count} 个独立来源</span>
              <span>{event.message_count} 条证据</span>
              {event.first_published_at && (
                <time dateTime={event.first_published_at}>
                  始于 {new Date(event.first_published_at).toLocaleDateString("zh-CN")}
                </time>
              )}
              <Link href={`/events/${event.id}`}>查看时间线 <ArrowUpRight size={13} /></Link>
            </div>
          </article>
        ))}
      </section>
      <footer><span>LoL Daily Intel · Reviewed events</span><span>Messages remain independent evidence.</span></footer>
    </main>
  );
}
