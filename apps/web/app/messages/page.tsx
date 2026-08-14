import { Search } from "lucide-react";
import Link from "next/link";
import { MessageFeed } from "@/components/message-feed";
import { PublicPagination, PublicSortControls } from "@/components/public-list-controls";
import { PublicShell } from "@/components/public-shell";
import { getPublishedDays, getPublishedItemsPage } from "@/lib/api";
import {
  normalizePage,
  normalizePublicSortBy,
  normalizeSortDirection,
  publicListHref,
  type PublicSearchParams,
} from "@/lib/public-list";
import { publicLabel } from "@/lib/public-labels";

const PAGE_SIZE = 25;
const productOptions = ["lol_pc", "tft", "lol_esports", "lol_universe", "other_lol_product", "riot_ecosystem", "unknown"];

export default async function MessagesPage({ searchParams }: { searchParams: Promise<Record<string, string | undefined>> }) {
  const params = await searchParams;
  const product = productOptions.includes(params.product ?? "") ? params.product : undefined;
  const messageType = params.message_type?.trim() || undefined;
  const search = params.search?.trim() || undefined;
  const date = /^\d{4}-\d{2}-\d{2}$/.test(params.date ?? "") ? params.date : undefined;
  const featured = params.featured === "true";
  const sortBy = normalizePublicSortBy(params.sort_by);
  const sort = normalizeSortDirection(params.sort);
  const page = normalizePage(params.page);
  const offset = (page - 1) * PAGE_SIZE;
  const filters = { product, messageType, search, date, featured, sortBy, sort, timezone: "Asia/Shanghai" };
  const [messagePage, publishedDays] = await Promise.all([
    getPublishedItemsPage(PAGE_SIZE, offset, filters),
    getPublishedDays(20, filters),
  ]);
  const currentParams: PublicSearchParams = {
    product,
    message_type: messageType,
    search,
    date,
    featured: featured ? "true" : undefined,
    sort_by: sortBy,
    sort,
    page: page > 1 ? String(page) : undefined,
  };
  const returnTo = publicListHref("/messages", currentParams, {});

  return (
    <PublicShell className="messages-page">
      <section className="public-page-intro public-frame compact-intro">
        <p className="ln-eyebrow"><i /> Reviewed Dispatches</p>
        <div><h1>消息归档</h1><p>快速检索已公开消息；筛选条件保留在网址中，打开详情后可以原路返回。</p></div>
      </section>

      <section className="message-workspace public-frame">
        <form className="public-filter-panel" action="/messages" method="get">
          <label className="public-search-field"><span>搜索</span><div><Search size={15} /><input name="search" defaultValue={search} placeholder="标题、摘要或消息 ID" /></div></label>
          <label><span>产品</span><select name="product" defaultValue={product ?? ""}><option value="">全部产品</option>{productOptions.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</select></label>
          <label><span>消息类型</span><select name="message_type" defaultValue={messageType ?? ""}><option value="">全部类型</option>{messagePage.message_type_options.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</select></label>
          <label><span>日期</span><input type="date" name="date" defaultValue={date} /></label>
          <label className="public-check-field"><input type="checkbox" name="featured" value="true" defaultChecked={featured} /><span>仅看精选</span></label>
          <input type="hidden" name="sort_by" value={sortBy} />
          <input type="hidden" name="sort" value={sort} />
          <div className="public-filter-actions"><button type="submit">应用筛选</button><Link href="/messages">清除</Link></div>
        </form>

        {publishedDays.days.length > 0 && (
          <nav className="published-days" aria-label="日期归档">
            <span>近期归档</span>
            <Link className={!date ? "active" : ""} href={publicListHref("/messages", currentParams, { date: null, page: null })}>全部</Link>
            {publishedDays.days.slice(0, 8).map((day) => <Link className={date === day.date ? "active" : ""} href={publicListHref("/messages", currentParams, { date: day.date, page: null })} key={day.date}><b>{day.date.slice(5)}</b><small>{day.count}</small></Link>)}
          </nav>
        )}

        <div className="public-list-head">
          <div><span>{messagePage.total} 条结果</span><strong>{date ? `${date} 的公开消息` : "全部公开消息"}</strong></div>
          <PublicSortControls pathname="/messages" searchParams={currentParams} sortBy={sortBy} sort={sort} />
        </div>
        <MessageFeed items={messagePage.items} startIndex={offset} returnTo={returnTo} returnLabel="返回消息列表" />
        <PublicPagination pathname="/messages" searchParams={currentParams} page={page} pageSize={PAGE_SIZE} total={messagePage.total} />
      </section>
    </PublicShell>
  );
}
