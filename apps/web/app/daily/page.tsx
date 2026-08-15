import { ArrowLeft, ArrowRight, CalendarDays, FileText } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { BackToTop } from "@/components/back-to-top";
import { firstMessageImage, MessageCard } from "@/components/message-feed";
import { PublicPageMasthead } from "@/components/public-page-masthead";
import { PublicShell } from "@/components/public-shell";
import { SectionTitle } from "@/components/section-title";
import { getDailyReport, getDailyReports } from "@/lib/api";
import { resolveImageSrc } from "@/lib/image-src";
import { publicLabel } from "@/lib/public-labels";
import type { DailyReport, PublishedItem } from "@/lib/types";

const sections: Array<{ key: keyof DailyReport["sections"]; label: string; subtitle: string }> = [
  { key: "lolpc", label: "英雄联盟", subtitle: "召唤师峡谷与游戏内容" },
  { key: "esports", label: "英雄联盟电竞", subtitle: "赛场、赛程与俱乐部动态" },
  { key: "tft", label: "云顶之弈", subtitle: "版本、玩法与生态" },
  { key: "other", label: "符文之地及其他", subtitle: "世界观、产品与拳头生态" },
];

function shanghaiDateOffset(days: number): string {
  const now = new Date();
  now.setUTCDate(now.getUTCDate() + days);
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

function adjacentDate(value: string, days: number): string {
  const date = new Date(`${value}T12:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function reportLead(report: DailyReport): PublishedItem | undefined {
  return Object.values(report.sections).flat().sort((a, b) => b.importance_score - a.importance_score)[0];
}

export default async function DailyReportPage({ searchParams }: { searchParams: Promise<{ date?: string }> }) {
  const params = await searchParams;
  const reports = await getDailyReports();
  const latestDate = reports.find((entry) => entry.status === "published")?.report_date ?? shanghaiDateOffset(-1);
  const reportDate = /^\d{4}-\d{2}-\d{2}$/.test(params.date ?? "") ? params.date! : latestDate;
  const report = await getDailyReport(reportDate);
  const lead = report ? reportLead(report) : undefined;
  const leadImage = lead ? firstMessageImage(lead) : undefined;
  const returnTo = `/daily?date=${reportDate}`;

  return (
    <PublicShell className="daily-page">
      <PublicPageMasthead
        eyebrow="LeagueNews Daily"
        title="每日纪要"
        description="将一天里值得继续关注的公开消息，整理成更从容的阅读次序。"
      >
        <div className="daily-date-bar">
          <Link href={`/daily?date=${adjacentDate(reportDate, -1)}`} aria-label="前一天"><ArrowLeft size={16} /></Link>
          <form action="/daily" method="get"><CalendarDays size={15} /><input type="date" name="date" defaultValue={reportDate} /><button type="submit">查看</button></form>
          <Link href={`/daily?date=${adjacentDate(reportDate, 1)}`} aria-label="后一天"><ArrowRight size={16} /></Link>
          {reportDate !== latestDate && <Link className="daily-latest-link" href={`/daily?date=${latestDate}`}>返回最新日报</Link>}
        </div>
      </PublicPageMasthead>

      {!report ? (
        <section className="daily-report-empty public-frame">
          <FileText size={24} /><p className="ln-eyebrow">{reportDate}</p><h2>这一天没有公开日报</h2><p>可以切换到前一天，或返回最新已经进入公开阅读流程的日报。</p><Link className="ln-primary-link" href={`/daily?date=${latestDate}`}>阅读最新日报 <ArrowRight size={14} /></Link>
        </section>
      ) : (
        <div className="public-frame daily-report-content">
          {lead && <section className={`daily-lead ${leadImage ? "has-image" : ""}`}>
            {leadImage && <div className="daily-lead-image"><Image src={resolveImageSrc(leadImage.storage_path)} alt="" fill sizes="(max-width: 760px) 100vw, 50vw" unoptimized referrerPolicy="no-referrer" /></div>}
            <div className="daily-lead-copy"><p className="ln-eyebrow"><i /> 当日优先阅读</p><div className="ln-card-labels"><span>{publicLabel(lead.products[0] ?? "unknown")}</span><span>重要性 {Math.round(lead.importance_score * 100)}</span><span>{lead.source_name}</span></div><h2><Link href={`/messages/${lead.id}?from=${encodeURIComponent(returnTo)}&fromLabel=${encodeURIComponent("返回日报")}`}>{lead.title}</Link></h2><p>{lead.summary}</p><Link className="ln-text-link" href={`/messages/${lead.id}?from=${encodeURIComponent(returnTo)}&fromLabel=${encodeURIComponent("返回日报")}`}>阅读完整消息 <ArrowRight size={14} /></Link></div>
          </section>}

          <div className="daily-report-sections">
            {sections.map((section) => {
              const items = report.sections[section.key].filter((item) => item.id !== lead?.id);
              if (!items.length) return null;
              return <section className="daily-report-section" key={section.key}><SectionTitle eyebrow={section.subtitle} title={section.label} aside={<span>{items.length} 条</span>} /><div className="daily-story-list">{items.map((item, index) => <MessageCard item={item} index={index} compact returnTo={returnTo} returnLabel="返回日报" key={item.id} />)}</div></section>;
            })}
          </div>
        </div>
      )}
      <BackToTop />
    </PublicShell>
  );
}
