import Link from "next/link";
import {
  publicListHref,
  type PublicSearchParams,
  type EventSortBy,
  type SortDirection,
} from "@/lib/public-list";

const messageSortOptions: Array<{
  sortBy: EventSortBy;
  sort: SortDirection;
  label: string;
}> = [
  { sortBy: "time", sort: "desc", label: "时间 ↓" },
  { sortBy: "time", sort: "asc", label: "时间 ↑" },
  { sortBy: "importance", sort: "desc", label: "重要性 ↓" },
  { sortBy: "importance", sort: "asc", label: "重要性 ↑" },
];

export function PublicSortControls({
  pathname,
  searchParams,
  sortBy,
  sort,
  includeHeat = false,
}: {
  pathname: string;
  searchParams: PublicSearchParams;
  sortBy: EventSortBy;
  sort: SortDirection;
  includeHeat?: boolean;
}) {
  const sortOptions = includeHeat
    ? [...messageSortOptions, { sortBy: "heat" as const, sort: "desc" as const, label: "热度 ↓" }]
    : messageSortOptions;
  return (
    <nav className="public-sort-controls" aria-label="列表排序">
      <span>排序</span>
      {sortOptions.map((option) => {
        const active = option.sortBy === sortBy && option.sort === sort;
        return (
          <Link
            className={active ? "active" : ""}
            href={publicListHref(pathname, searchParams, {
              sort_by: option.sortBy,
              sort: option.sort,
              page: null,
            })}
            aria-current={active ? "page" : undefined}
            key={`${option.sortBy}-${option.sort}`}
          >
            {option.label}
          </Link>
        );
      })}
    </nav>
  );
}

export function PublicPagination({
  pathname,
  searchParams,
  page,
  pageSize,
  total,
}: {
  pathname: string;
  searchParams: PublicSearchParams;
  page: number;
  pageSize: number;
  total: number;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, totalPages);
  if (totalPages <= 1) return null;

  return (
    <nav className="public-pagination" aria-label="列表分页">
      {currentPage > 1 ? (
        <Link
          href={publicListHref(pathname, searchParams, {
            page: currentPage === 2 ? null : String(currentPage - 1),
          })}
        >
          上一页
        </Link>
      ) : <span />}
      <span>第 {currentPage} / {totalPages} 页</span>
      {currentPage < totalPages ? (
        <Link
          href={publicListHref(pathname, searchParams, {
            page: String(currentPage + 1),
          })}
        >
          下一页
        </Link>
      ) : <span />}
    </nav>
  );
}
