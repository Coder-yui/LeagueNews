import Link from "next/link";
import { getDigests } from "@/lib/api";

export default async function DigestsPage() {
  const digests = await getDigests();
  return (
    <main>
      <header className="site-header">
        <Link className="brand" href="/">
          <span className="brand-mark">LD</span>
          <span>LoL Daily Intel</span>
        </Link>
        <nav aria-label="主要导航">
          <Link href="/">消息</Link>
          <Link href="/events">事件</Link>
          <Link className="active" href="/digests">日报</Link>
        </nav>
      </header>
      <section className="event-page-head">
        <span className="kicker">EVENT INTELLIGENCE DIGESTS</span>
        <h1>日报与周报</h1>
        <p>只依据已发布事件的 revision 生成；晚到消息以新 revision 修订。</p>
      </section>
      <section className="public-event-list">
        {!digests.length && <div className="message-empty">目前还没有已发布摘要。</div>}
        {digests.map((digest) => (
          <article className="public-event-card" key={digest.id}>
            <div className="event-topline">
              <span>{digest.digest_type === "daily" ? "日报" : "周报"}</span>
              <span>Revision {digest.current_revision}</span>
              <time dateTime={digest.cutoff_at}>
                截止 {new Date(digest.cutoff_at).toLocaleString("zh-CN")}
              </time>
            </div>
            <h2><Link href={`/digests/${digest.id}`}>{digest.title}</Link></h2>
            <p>{digest.body.split("\n").find(Boolean)}</p>
          </article>
        ))}
      </section>
    </main>
  );
}
