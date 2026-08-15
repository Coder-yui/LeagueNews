import { Search } from "lucide-react";
import Link from "next/link";
import { BackToTop } from "@/components/back-to-top";
import { MessageFeed } from "@/components/message-feed";
import { PublishedDays } from "@/components/published-days";
import { PublicPagination, PublicSortControls } from "@/components/public-list-controls";
import { PublicPageMasthead } from "@/components/public-page-masthead";
import { PublicSelect } from "@/components/public-select";
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
      <PublicPageMasthead
        eyebrow="Reviewed Dispatches"
        title="消息归档"
        description="快速检索已公开消息；筛选条件保留在网址中，打开详情后可以原路返回。"
      />

      <section className="message-workspace public-frame">
        <form className="public-filter-panel" action="/messages" method="get">
          <label className="public-search-field"><span>搜索</span><div><Search size={15} /><input name="search" defaultValue={search} placeholder="标题、摘要或消息 ID" /></div></label>
          <PublicSelect label="产品" name="product" defaultValue={product ?? ""}><option value="">全部产品</option>{productOptions.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
          <PublicSelect label="消息类型" name="message_type" defaultValue={messageType ?? ""}><option value="">全部类型</option>{messagePage.message_type_options.map((value) => <option value={value} key={value}>{publicLabel(value)}</option>)}</PublicSelect>
          <label><span>日期</span><input type="date" name="date" defaultValue={date} /></label>
          <label className="public-check-field"><input type="checkbox" name="featured" value="true" defaultChecked={featured} /><span>仅看精选</span></label>
          <input type="hidden" name="sort_by" value={sortBy} />
          <input type="hidden" name="sort" value={sort} />
          <div className="public-filter-actions"><button type="submit">应用筛选</button><Link href="/messages">清除</Link></div>
        </form>

        {publishedDays.days.length > 0 && (
          <PublishedDays
            allHref={publicListHref("/messages", currentParams, { date: null, page: null })}
            days={publishedDays.days.map((day) => ({
              count: day.count,
              date: day.date,
              href: publicListHref("/messages", currentParams, { date: day.date, page: null }),
            }))}
            selectedDate={date}
          />
        )}

        <div className="public-list-head">
          <div><span>{messagePage.total} 条结果</span><strong>{date ? `${date} 的公开消息` : "全部公开消息"}</strong></div>
          <PublicSortControls pathname="/messages" searchParams={currentParams} sortBy={sortBy} sort={sort} />
        </div>
        <MessageFeed items={messagePage.items} startIndex={offset} returnTo={returnTo} returnLabel="返回消息列表" />
        <PublicPagination pathname="/messages" searchParams={currentParams} page={page} pageSize={PAGE_SIZE} total={messagePage.total} />
      </section>
      <BackToTop />
    </PublicShell>
  );
}
