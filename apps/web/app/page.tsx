import {
  Activity,
  CalendarDays,
  Database,
  Radio,
} from "lucide-react";
import Link from "next/link";
import { MessageFeed } from "@/components/message-feed";
import { getAllPublishedItems } from "@/lib/api";

export default async function Home() {
  const messagePage = await getAllPublishedItems();
  const items = messagePage.items;
  const topItem = items[0];
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
          <a className="active" href="#messages">消息</a>
          <a href="#pipeline">处理链路</a>
          <a href="#sources">信源</a>
          <a href="/admin">处理台</a>
        </nav>
        <div className="live-state"><span /> Workflow online</div>
      </header>

      <section id="top" className="hero">
        <div className="eyebrow"><CalendarDays size={15} /> {dateLabel} · 每日简报</div>
        <h1>峡谷内外，<br /><em>值得关注的事。</em></h1>
        <p>展示经过 AI 处理与人工审核的单条消息。保留完整原文、中文译文、图片和可追溯的结构化结果。</p>
      </section>

      <section className="signal-grid" aria-label="今日概览">
        <div><span>已审消息</span><strong>{messagePage.total.toString().padStart(2, "0")}</strong></div>
        <div><span>高优先级</span><strong>{items.filter((item) => item.importance_score >= 0.8).length.toString().padStart(2, "0")}</strong></div>
        <div><span>已显示消息</span><strong>{items.length.toString().padStart(2, "0")}</strong></div>
        <div className="pulse"><Activity size={18} /><span>持续更新</span></div>
      </section>

      {topItem && (
        <section className="lead-story">
          <div className="lead-meta"><Radio size={16} /> 最新消息 · 重要性 {Math.round(topItem.importance_score * 100)}</div>
          <h2><Link href={`/messages/${topItem.id}`}>{topItem.title}</Link></h2>
          <p>{topItem.summary}</p>
          <div className="tag-row">
            <span>#{topItem.id}</span>
            <span>{topItem.message_type}</span>
          </div>
        </section>
      )}

      <section id="messages" className="messages-section">
        <div className="section-heading">
          <div><span className="kicker">REVIEWED STREAM</span><h2>已审核消息</h2></div>
          <span>按原始发布时间排序</span>
        </div>
        <MessageFeed items={items} />
      </section>

      <section id="pipeline" className="pipeline">
        <div><Database size={22} /><strong>可追踪的数据链</strong><span>采集与分析分层，所有结论回到原始内容。</span></div>
        <div className="pipeline-flow"><span>CONNECTOR</span><i /> <span>RAW ITEM</span><i /> <span>REVIEWED ITEM</span><i /> <span>MESSAGE</span></div>
      </section>

      <section id="sources" className="source-summary">
        <span>全部信源</span>
        <strong>{new Set(items.map((item) => item.source_name)).size}</strong>
        <p>不同平台账号保持独立信源身份，所有消息均可返回原始位置核验。</p>
      </section>

      <footer><span>LoL Daily Intel · Reviewed messages</span><span>Built for signals, not noise.</span></footer>
    </main>
  );
}
