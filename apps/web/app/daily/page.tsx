import { CalendarDays, FileText } from "lucide-react";
import Link from "next/link";
import { MessageFeed } from "@/components/message-feed";
import { getDailyReport } from "@/lib/api";
import type { DailyReport } from "@/lib/types";

const sections: Array<{ key: keyof DailyReport["sections"]; label: string; limit: number }> = [
  { key: "lolpc", label: "LoL PC", limit: 5 },
  { key: "esports", label: "Esports", limit: 3 },
  { key: "tft", label: "TFT", limit: 3 },
  { key: "other", label: "其他", limit: 3 },
];

function shanghaiDate(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

export default async function DailyReportPage({
  searchParams,
}: {
  searchParams: Promise<{ date?: string }>;
}) {
  const params = await searchParams;
  const reportDate = params.date ?? shanghaiDate();
  const report = await getDailyReport(reportDate);

  return (
    <main>
      <header className="site-header">
        <Link className="brand" href="/" aria-label="LeagueNews 首页">
          <span className="brand-mark">LD</span>
          <span>LoL Daily Intel</span>
        </Link>
        <nav aria-label="主要导航">
          <Link href="/">消息</Link>
          <Link className="active" href="/daily">日报</Link>
          <Link href="/events">事件</Link>
          <Link href="/admin">处理台</Link>
        </nav>
        <div className="live-state"><span /> Daily report</div>
      </header>

      <section className="hero daily-report-hero">
        <div className="eyebrow"><CalendarDays size={15} /> DAILY REPORT · 精选消息</div>
        <h1>LeagueNews<br /><em>日报</em></h1>
        <p>{reportDate} · 只展示当天已处理完成、原创且重要性达到 60 的消息。</p>
      </section>

      {!report ? (
        <section className="daily-report-empty">
          <FileText size={22} />
          <h2>这一天还没有日报</h2>
          <p>请先通过日报生成接口生成 {reportDate} 的内容。</p>
        </section>
      ) : (
        <section className="daily-report-sections">
          {sections.map((section) => {
            const items = report.sections[section.key];
            if (!items.length) return null;
            return (
              <section className="daily-report-section" key={section.key}>
                <div className="section-heading">
                  <div><span className="kicker">TOP {section.limit}</span><h2>{section.label}</h2></div>
                  <span>{items.length} 条精选</span>
                </div>
                <MessageFeed items={items} />
              </section>
            );
          })}
        </section>
      )}

      <footer><span>LoL Daily Intel · Daily report</span><Link href="/">返回消息流</Link></footer>
    </main>
  );
}
