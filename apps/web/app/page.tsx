import { ArrowRight, CalendarDays, Radio } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { EventCard } from "@/components/event-card";
import { firstMessageImage, MessageCard } from "@/components/message-feed";
import { PublicShell } from "@/components/public-shell";
import { SectionTitle } from "@/components/section-title";
import { getDailyReport, getDailyReports, getEventsPage, getPublishedItemsPage } from "@/lib/api";
import { formatPublicTime, publicLabel } from "@/lib/public-labels";

function shanghaiDateOffset(days: number): string {
  const now = new Date();
  now.setUTCDate(now.getUTCDate() + days);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now);
}

export default async function Home() {
  const fallbackDate = shanghaiDateOffset(-1);
  const reports = await getDailyReports();
  const digestDate = reports.find((entry) => entry.status === "published")?.report_date ?? fallbackDate;
  const [featuredPage, latestPage, eventPage, daily] = await Promise.all([
    getPublishedItemsPage(1, 0, { featured: true, sortBy: "importance", sort: "desc" }),
    getPublishedItemsPage(7, 0, { sortBy: "time", sort: "desc" }),
    getEventsPage(3, 0, { sortBy: "time", sort: "desc" }),
    getDailyReport(digestDate),
  ]);
  const lead = featuredPage.items[0] ?? latestPage.items[0];
  const leadImage = lead ? firstMessageImage(lead) : undefined;
  const latest = latestPage.items.filter((item) => item.id !== lead?.id).slice(0, 5);
  const dailyCount = daily
    ? Object.values(daily.sections).reduce((count, items) => count + items.length, 0)
    : 0;

  return (
    <PublicShell className="home-page">
      <section className={`home-lead ${leadImage ? "has-image" : ""}`}>
        {leadImage && (
          <div className="home-lead-image" aria-hidden="true">
            <Image src={leadImage.storage_path ?? leadImage.source_url ?? ""} alt="" fill sizes="100vw" priority unoptimized />
          </div>
        )}
        <div className="home-lead-overlay" />
        <div className="home-lead-inner">
          <p className="ln-eyebrow"><Radio size={13} /> 当前优先阅读</p>
          {lead ? (
            <>
              <div className="home-lead-meta">
                <span>{publicLabel(lead.products[0] ?? "unknown")}</span>
                <span>{publicLabel(lead.message_type)}</span>
                <time dateTime={lead.published_at ?? lead.created_at}>{formatPublicTime(lead.published_at ?? lead.created_at)}</time>
              </div>
              <h1><Link href={`/messages/${lead.id}?from=%2F&fromLabel=${encodeURIComponent("返回首页")}`}>{lead.title}</Link></h1>
              <p className="home-lead-summary">{lead.summary}</p>
              <Link className="ln-primary-link" href={`/messages/${lead.id}?from=%2F&fromLabel=${encodeURIComponent("返回首页")}`}>阅读完整消息 <ArrowRight size={16} /></Link>
            </>
          ) : (
            <><h1>峡谷内外，<br />值得记录的事。</h1><p className="home-lead-summary">经过处理与审核的 League of Legends 资讯，会在这里形成可追溯的记录。</p></>
          )}
        </div>
      </section>

      <section className="home-intro public-frame">
        <p className="ln-eyebrow"><i /> The Living Record</p>
        <h2>不止追逐每一条消息，<br />也持续记录事情如何发生。</h2>
        <p>LeagueNews 将单条消息、连续事件与每日精选分层呈现：快速浏览时保持清晰，需要深入时保留来源与上下文。</p>
      </section>

      <section className="home-events public-frame">
        <SectionTitle eyebrow="Developing Stories" title="正在发展的事件" aside={<Link href="/events">查看全部事件 <ArrowRight size={14} /></Link>} />
        {eventPage.items.length > 0 ? (
          <div className="home-event-grid">
            {eventPage.items.map((event, index) => <EventCard event={event} featured={index === 0} returnTo="/" key={event.id} />)}
          </div>
        ) : <div className="message-empty">当前没有正在公开追踪的事件。</div>}
      </section>

      <section className="home-latest public-frame">
        <SectionTitle eyebrow="Latest Dispatches" title="最新消息" aside={<Link href="/messages">进入消息归档 <ArrowRight size={14} /></Link>} />
        <div className="home-latest-list">
          {latest.map((item, index) => <MessageCard item={item} index={index} compact returnTo="/" returnLabel="返回首页" key={item.id} />)}
        </div>
      </section>

      <section className="home-digest public-frame">
        <div>
          <p className="ln-eyebrow"><CalendarDays size={13} /> Daily Digest · {digestDate}</p>
          <h2>把一天的噪声，<br />整理成一份可读的纪要。</h2>
        </div>
        <div>
          <strong>{dailyCount.toString().padStart(2, "0")}</strong>
          <span>{daily ? "条最新日报精选" : "暂无已发布日报"}</span>
          <Link className="ln-text-link" href={`/daily?date=${digestDate}`}>阅读最新简报 <ArrowRight size={14} /></Link>
        </div>
      </section>
    </PublicShell>
  );
}
