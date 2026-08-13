import {
  Activity,
  CalendarDays,
  Database,
  Radio,
} from "lucide-react";
import Link from "next/link";
import { MessageFeed } from "@/components/message-feed";
import { PublicPagination, PublicSortControls } from "@/components/public-list-controls";
import { getPublishedItemsPage } from "@/lib/api";
import {
  normalizePage,
  normalizePublicSortBy,
  normalizeSortDirection,
  publicListHref,
  type PublicSearchParams,
} from "@/lib/public-list";

const PAGE_SIZE = 25;
const productTabs = [
  ["all", "全部产品"],
  ["lol_pc", "英雄联盟"],
  ["tft", "云顶之弈"],
  ["lol_esports", "英雄联盟电竞"],
  ["lol_universe", "英雄联盟宇宙"],
  ["other_lol_product", "其他 LoL 产品"],
  ["riot_ecosystem", "拳头生态"],
  ["unknown", "未分类"],
] as const;

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{
    featured?: string;
    product?: string;
    sort_by?: string;
    sort?: string;
    page?: string;
  }>;
}) {
  const params = await searchParams;
  const featuredParam = params.featured;
  const featured = featuredParam === "true";
  const activeProduct = productTabs.some(([value]) => value === params.product)
    ? params.product as (typeof productTabs)[number][0]
    : "all";
  const product = activeProduct === "all" ? undefined : activeProduct;
  const sortBy = normalizePublicSortBy(params.sort_by);
  const sort = normalizeSortDirection(params.sort);
  const page = normalizePage(params.page);
  const offset = (page - 1) * PAGE_SIZE;
  const currentParams: PublicSearchParams = {
    featured: featured ? "true" : undefined,
    product,
    sort_by: sortBy,
    sort,
    page: page > 1 ? String(page) : undefined,
  };
  const messagePage = await getPublishedItemsPage(PAGE_SIZE, offset, {
    featured,
    product,
    sortBy,
    sort,
  });
  const items = messagePage.items;
  const topItem = items[0];
  const leadLabel = page > 1
    ? `第 ${page} 页首条`
    : sortBy === "importance"
      ? sort === "desc" ? "最高重要性" : "较低重要性"
      : sort === "desc" ? "最新消息" : "最早消息";
  const sortDescription = `${sortBy === "time" ? "原始发布时间" : "重要性"}${sort === "desc" ? "降序" : "升序"}`;
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
          <Link href="/daily">日报</Link>
          <Link href="/events">事件</Link>
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
        <div><span>筛选结果</span><strong>{messagePage.total.toString().padStart(2, "0")}</strong></div>
        <div><span>本页高重要性</span><strong>{items.filter((item) => item.importance_score >= 0.8).length.toString().padStart(2, "0")}</strong></div>
        <div><span>本页消息</span><strong>{items.length.toString().padStart(2, "0")}</strong></div>
        <div className="pulse"><Activity size={18} /><span>持续更新</span></div>
      </section>

      {topItem && (
        <section className="lead-story">
          <div className="lead-meta"><Radio size={16} /> {leadLabel} · 重要性 {Math.round(topItem.importance_score * 100)}</div>
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
          <span>按{sortDescription}</span>
        </div>
        <nav className="event-category-tabs" aria-label="消息产品分类">
          {productTabs.map(([value, label]) => {
            const active = activeProduct === value;
            return (
              <Link
                className={active ? "active" : ""}
                href={publicListHref("/", currentParams, {
                  product: value === "all" ? null : value,
                  page: null,
                })}
                aria-current={active ? "page" : undefined}
                key={value}
              >
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="public-list-toolbar">
          <nav className="public-feature-filter" aria-label="消息范围">
            <Link
              className={!featured ? "active" : ""}
              href={publicListHref("/", currentParams, { featured: null, page: null })}
            >
              全部消息
            </Link>
            <Link
              className={featured ? "active" : ""}
              href={publicListHref("/", currentParams, { featured: "true", page: null })}
            >
              精选消息
            </Link>
          </nav>
          <PublicSortControls
            pathname="/"
            searchParams={currentParams}
            sortBy={sortBy}
            sort={sort}
          />
        </div>
        <MessageFeed items={items} startIndex={offset} />
        <PublicPagination
          pathname="/"
          searchParams={currentParams}
          page={page}
          pageSize={PAGE_SIZE}
          total={messagePage.total}
        />
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
