import Link from "next/link";
import { notFound } from "next/navigation";
import { getDigest } from "@/lib/api";

export default async function DigestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const digest = await getDigest(Number(id));
  if (!digest) notFound();
  return (
    <main>
      <header className="site-header">
        <Link className="brand" href="/digests">
          <span className="brand-mark">LD</span>
          <span>返回日报</span>
        </Link>
      </header>
      <article className="event-page-head">
        <span className="kicker">
          {digest.digest_type === "daily" ? "DAILY" : "WEEKLY"} · REVISION{" "}
          {digest.current_revision}
        </span>
        <h1>{digest.title}</h1>
        <p>时区 {digest.timezone} · 截止 {new Date(digest.cutoff_at).toLocaleString("zh-CN")}</p>
      </article>
      <section className="public-event-list">
        {digest.input_snapshot.map((event) => (
          <article className="public-event-card" key={`${event.event_id}-${event.event_revision}`}>
            <h2><Link href={`/events/${event.event_id}`}>{event.title}</Link></h2>
            <p>{event.summary}</p>
            <div className="public-event-meta">
              <span>事件 revision {event.event_revision}</span>
              <span>重要性 {Math.round(event.importance_score * 100)}</span>
            </div>
          </article>
        ))}
      </section>
    </main>
  );
}
