import { ArrowRight, CalendarDays, Radio } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { EventCard } from "@/components/event-card";
import { FeaturedCarousel } from "@/components/featured-carousel";
import { MessageCard } from "@/components/message-feed";
import { PublicShell } from "@/components/public-shell";
import { SectionTitle } from "@/components/section-title";
import { getDailyReport, getDailyReports, getEventsPage, getPublishedItemsPage } from "@/lib/api";

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
  const currentDate = shanghaiDateOffset(0);
  const fallbackDate = shanghaiDateOffset(-1);
  const reports = await getDailyReports();
  const digestDate = reports.find((entry) => entry.status === "published")?.report_date ?? fallbackDate;
  const [featuredPage, latestPage, eventPage, daily] = await Promise.all([
    getPublishedItemsPage(5, 0, { featured: true, sortBy: "time", sort: "desc" }),
    getPublishedItemsPage(16, 0, { sortBy: "time", sort: "desc" }),
    getEventsPage(3, 0, { sortBy: "time", sort: "desc" }),
    getDailyReport(digestDate),
  ]);
  const spotlight = featuredPage.items.slice(0, 5);
  const lead = spotlight[0];
  const spotlightIds = new Set(spotlight.map((item) => item.id));
  const latest = latestPage.items.filter((item) => !spotlightIds.has(item.id)).slice(0, 8);
  const dailyCount = daily
    ? Object.values(daily.sections).reduce((count, items) => count + items.length, 0)
    : 0;

  return (
    <PublicShell className="home-page">
      <section className="home-newsroom public-frame">
        <header className="home-newsroom-head">
          <div>
            <p className="ln-eyebrow"><Radio size={13} /> Editor&apos;s selection</p>
            <h1>精选消息</h1>
          </div>
          <div className="home-newsroom-context">
            <span>{currentDate} · 已收录 {featuredPage.total} 条精选</span>
            <Link href="/messages?featured=true&sort_by=importance&sort=desc">查看全部精选 <ArrowRight size={14} /></Link>
          </div>
        </header>

        {lead ? (
          <div className="home-spotlight">
            <FeaturedCarousel items={spotlight} />
          </div>
        ) : <div className="message-empty">当前没有可公开阅读的精选消息。</div>}
      </section>

      <section className="home-latest public-frame">
        <SectionTitle eyebrow="Live wire" title="最新发布" aside={<Link href="/messages">进入消息归档 <ArrowRight size={14} /></Link>} />
        <div className="home-latest-list">
          {latest.map((item, index) => <MessageCard item={item} index={index} compact stream returnTo="/" returnLabel="返回首页" key={item.id} />)}
        </div>
      </section>

      <section className="home-events public-frame">
        <SectionTitle eyebrow="Developing stories" title="正在跟进" aside={<Link href="/events">查看全部事件 <ArrowRight size={14} /></Link>} />
        {eventPage.items.length > 0 ? (
          <div className="home-event-grid">
            {eventPage.items.map((event) => <EventCard event={event} returnTo="/" key={event.id} />)}
          </div>
        ) : <div className="message-empty">当前没有正在公开追踪的事件。</div>}
      </section>

      <section className="home-digest public-frame">
        <div className="home-digest-copy">
          <p className="ln-eyebrow"><CalendarDays size={13} /> Daily Digest · {digestDate}</p>
          <h2>今天发生了什么，<br />一份简报读完。</h2>
        </div>
        <div className="home-digest-meta">
          <strong>{dailyCount.toString().padStart(2, "0")}</strong>
          <span>{daily ? "条最新日报精选" : "暂无已发布日报"}</span>
          <Link className="ln-text-link" href={`/daily?date=${digestDate}`}>阅读最新简报 <ArrowRight size={14} /></Link>
        </div>
        <div className="home-digest-art" aria-hidden="true">
          <Image className="home-digest-art-light" src="/images/heimerdinger-diary.png" alt="" fill sizes="(max-width: 760px) 100vw, 700px" />
          <Image className="home-digest-art-dark" src="/images/lux-attic-daily-dark.jpg" alt="" fill sizes="(max-width: 760px) 100vw, 700px" />
        </div>
      </section>
    </PublicShell>
  );
}
