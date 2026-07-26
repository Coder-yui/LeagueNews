import {
  Activity,
  CalendarDays,
  Database,
  Radio,
  Sparkles,
} from "lucide-react";
import { getEvents } from "@/lib/api";
import { EventFeed } from "@/components/event-feed";

const credibilityLabel: Record<string, string> = {
  official: "官方确认",
  corroborated: "多源印证",
  unverified: "尚未证实",
  rumor: "社区传闻",
};

export default async function Home() {
  const { events, isDemo } = await getEvents();
  const topEvent = events[0];
  const dateLabel = new Intl.DateTimeFormat("zh-CN", {
    month: "long",
    day: "numeric",
    weekday: "long",
  }).format(new Date());

  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="LoL Daily Intel 首页">
          <span className="brand-mark">LD</span>
          <span>LoL Daily Intel</span>
        </a>
        <nav aria-label="主要导航">
          <a className="active" href="#digest">今日情报</a>
          <a href="#events">事件</a>
          <a href="#sources">信源</a>
          <a href="/admin">处理台</a>
        </nav>
        <div className="live-state"><span /> Workflow online</div>
      </header>

      <section id="digest" className="hero">
        <div className="eyebrow"><CalendarDays size={15} /> {dateLabel} · 每日简报</div>
        <h1>峡谷内外，<br /><em>值得关注的事。</em></h1>
        <p>从公告、赛事与社区讨论中提炼关键信号。保留来源脉络，也诚实标记不确定性。</p>
        {isDemo && <div className="demo-note"><Sparkles size={16} /> 当前展示样例数据；API 有真实事件后会自动切换。</div>}
      </section>

      <section className="signal-grid" aria-label="今日概览">
        <div><span>收录事件</span><strong>{events.length.toString().padStart(2, "0")}</strong></div>
        <div><span>高优先级</span><strong>{events.filter((event) => event.importance_score >= 0.8).length.toString().padStart(2, "0")}</strong></div>
        <div><span>可信信号</span><strong>{events.filter((event) => ["official", "corroborated"].includes(event.credibility)).length.toString().padStart(2, "0")}</strong></div>
        <div className="pulse"><Activity size={18} /><span>持续更新</span></div>
      </section>

      {topEvent && (
        <section className="lead-story">
          <div className="lead-meta"><Radio size={16} /> 今日头条 · 重要性 {Math.round(topEvent.importance_score * 100)}</div>
          <h2>{topEvent.title}</h2>
          <p>{topEvent.summary}</p>
          <div className="tag-row"><span>{topEvent.category}</span><span className={`cred ${topEvent.credibility}`}>{credibilityLabel[topEvent.credibility] ?? topEvent.credibility}</span></div>
        </section>
      )}

      <section id="events" className="events-section">
        <div className="section-heading">
          <div><span className="kicker">INTEL STREAM</span><h2>今日事件</h2></div>
          <span>按重要性排序</span>
        </div>
        <EventFeed events={events} />
      </section>

      <section id="sources" className="pipeline">
        <div><Database size={22} /><strong>可追踪的数据链</strong><span>采集与分析分层，所有结论回到原始内容。</span></div>
        <div className="pipeline-flow"><span>CONNECTOR</span><i /> <span>RAW ITEM</span><i /> <span>AI ANALYSIS</span><i /> <span>EVENT</span></div>
      </section>

      <footer><span>LoL Daily Intel · MVP</span><span>Built for signals, not noise.</span></footer>
    </main>
  );
}
