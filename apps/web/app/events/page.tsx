import { Search } from "lucide-react";
import Link from "next/link";
import { BackToTop } from "@/components/back-to-top";
import { EventCard } from "@/components/event-card";
import { PublicPagination, PublicSortControls } from "@/components/public-list-controls";
import { PublicPageMasthead } from "@/components/public-page-masthead";
import { PublicSelect } from "@/components/public-select";
import { PublicShell } from "@/components/public-shell";
import { getEventsPage } from "@/lib/api";
import {
  normalizeEventSortBy,
  normalizePage,
  normalizeSortDirection,
  publicListHref,
  type PublicSearchParams,
} from "@/lib/public-list";
import { publicLabel } from "@/lib/public-labels";

const PAGE_SIZE = 18;
const categories = ["esports", "lol_pc", "tft", "other_products", "ecosystem"];
const products = ["lol_pc", "tft", "lol_esports", "lol_universe", "other_lol_product", "riot_ecosystem"];
const families = ["gameplay_balance", "gameplay_release", "cosmetic_release", "player_activity", "commercial_offer", "service_incident", "security_enforcement", "esports_match", "esports_schedule", "roster_change", "esports_rules", "universe_release", "media_release", "corporate_change", "platform_service", "other_named_development"];
const lifecycles = ["unconfirmed", "developing", "confirmed", "disputed", "denied", "resolved", "stale"];
const credibilityLevels = ["unverified", "plausible", "corroborated", "officially_confirmed", "disputed", "denied"];
const importanceLevels = ["critical", "high", "medium", "low"];
const heatLevels = ["surging", "hot", "active", "emerging", "cold"];

function accepted(value: string | undefined, options: string[]) {
  return value && options.includes(value) ? value : undefined;
}

export default async function EventsPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const category = accepted(params.category, categories);
  const product = accepted(params.product, products);
  const eventFamily = accepted(params.event_family, families);
  const lifecycle = accepted(params.lifecycle, lifecycles);
  const credibilityLevel = accepted(params.credibility_level, credibilityLevels);
  const importanceLevel = accepted(params.importance_level, importanceLevels);
  const heatLevel = accepted(params.heat_level, heatLevels);
  const search = params.search?.trim() || undefined;
  const sortBy = normalizeEventSortBy(params.sort_by);
  const sort = normalizeSortDirection(params.sort);
  const pageNumber = normalizePage(params.page);
  const offset = (pageNumber - 1) * PAGE_SIZE;
  const currentParams: PublicSearchParams = {
    category, product, event_family: eventFamily, lifecycle,
    credibility_level: credibilityLevel, importance_level: importanceLevel,
    heat_level: heatLevel, search, sort_by: sortBy, sort,
    page: pageNumber > 1 ? String(pageNumber) : undefined,
  };
  const page = await getEventsPage(PAGE_SIZE, offset, { category, product, eventFamily, lifecycle, credibilityLevel, importanceLevel, heatLevel, search, sortBy, sort });
  const returnTo = publicListHref("/events", currentParams, {});

  return (
    <PublicShell className="events-page">
      <PublicPageMasthead
        eyebrow="Developing Record"
        title="事件追踪"
        description="同一进展被聚合为持续更新的事件。重要性、可信度与热度分别表达，不互相替代。"
      />

      <section className="event-workspace public-frame">
        <nav className="event-category-tabs" aria-label="事件分类">
          <Link className={!category ? "active" : ""} href={publicListHref("/events", currentParams, { category: null, page: null })}>全部事件</Link>
          {categories.map((value) => <Link className={category === value ? "active" : ""} href={publicListHref("/events", currentParams, { category: value, page: null })} key={value}>{publicLabel(value)}</Link>)}
        </nav>

        <form className="public-filter-panel event-filters" action="/events" method="get">
          <label className="public-search-field"><span>搜索事件</span><div><Search size={15} /><input name="search" defaultValue={search} placeholder="标题或当前摘要" /></div></label>
          <PublicSelect label="产品" name="product" defaultValue={product ?? ""}><option value="">全部产品</option>{products.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
          <PublicSelect label="事件族" name="event_family" defaultValue={eventFamily ?? ""}><option value="">全部事件族</option>{families.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
          <PublicSelect label="阶段" name="lifecycle" defaultValue={lifecycle ?? ""}><option value="">全部阶段</option>{lifecycles.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
          <details className="public-advanced-filters" open={Boolean(credibilityLevel || importanceLevel || heatLevel)}>
            <summary>高级筛选{credibilityLevel || importanceLevel || heatLevel ? " · 已应用" : ""}</summary>
            <div>
              <PublicSelect label="可信度" name="credibility_level" defaultValue={credibilityLevel ?? ""}><option value="">全部可信度</option>{credibilityLevels.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
              <PublicSelect label="重要性" name="importance_level" defaultValue={importanceLevel ?? ""}><option value="">全部重要性</option>{importanceLevels.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
              <PublicSelect label="热度" name="heat_level" defaultValue={heatLevel ?? ""}><option value="">全部热度</option>{heatLevels.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
            </div>
          </details>
          {category && <input type="hidden" name="category" value={category} />}
          <input type="hidden" name="sort_by" value={sortBy} /><input type="hidden" name="sort" value={sort} />
          <div className="public-filter-actions"><button type="submit">应用筛选</button><Link href="/events">清除</Link></div>
        </form>

        <div className="public-list-head">
          <div><span>{page.total} 个结果</span><strong>公开事件记录</strong></div>
          <PublicSortControls pathname="/events" searchParams={currentParams} sortBy={sortBy} sort={sort} includeHeat />
        </div>
        {page.items.length > 0 ? <div className="event-card-grid">{page.items.map((event) => <EventCard event={event} returnTo={returnTo} key={event.id} />)}</div> : <div className="message-empty">当前条件下没有公开事件。</div>}
        <PublicPagination pathname="/events" searchParams={currentParams} page={pageNumber} pageSize={PAGE_SIZE} total={page.total} />
      </section>
      <BackToTop />
    </PublicShell>
  );
}
