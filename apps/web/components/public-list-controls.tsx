import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import Link from "next/link";
import {
  publicListHref,
  type PublicSearchParams,
  type EventSortBy,
  type SortDirection,
} from "@/lib/public-list";

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
  const timeActive = sortBy === "time";
  const importanceActive = sortBy === "importance";
  const heatActive = sortBy === "heat";
  const nextTimeSort: SortDirection = timeActive && sort === "desc" ? "asc" : "desc";
  const nextImportance = !importanceActive
    ? { sort_by: "importance", sort: "desc" }
    : sort === "desc"
      ? { sort_by: "importance", sort: "asc" }
      : { sort_by: "time", sort: "desc" };
  const nextHeat = !heatActive
    ? { sort_by: "heat", sort: "desc" }
    : sort === "desc"
      ? { sort_by: "heat", sort: "asc" }
      : { sort_by: "time", sort: "desc" };
  const TimeIcon = timeActive && sort === "asc" ? ArrowUp : ArrowDown;
  const ImportanceIcon = !importanceActive ? ArrowUpDown : sort === "asc" ? ArrowUp : ArrowDown;
  const HeatIcon = !heatActive ? ArrowUpDown : sort === "asc" ? ArrowUp : ArrowDown;

  return (
    <nav className="public-sort-controls" aria-label="列表排序">
      <span>排序</span>
      <Link
        className={timeActive ? "active" : ""}
        href={publicListHref(pathname, searchParams, {
          sort_by: "time",
          sort: nextTimeSort,
          page: null,
        })}
        aria-current={timeActive ? "page" : undefined}
        aria-label={`按时间${nextTimeSort === "desc" ? "从新到旧" : "从旧到新"}排序`}
      >
        时间 <TimeIcon aria-hidden="true" size={13} />
      </Link>
      <Link
        className={importanceActive ? "active" : ""}
        href={publicListHref(pathname, searchParams, { ...nextImportance, page: null })}
        aria-current={importanceActive ? "page" : undefined}
        aria-label={
          !importanceActive
            ? "按重要性从高到低排序"
            : sort === "desc"
              ? "按重要性从低到高排序"
              : "取消重要性排序"
        }
      >
        重要性 <ImportanceIcon aria-hidden="true" size={13} />
      </Link>
      {includeHeat && (
        <Link
          className={heatActive ? "active" : ""}
          href={publicListHref(pathname, searchParams, { ...nextHeat, page: null })}
          aria-current={heatActive ? "page" : undefined}
          aria-label={
            !heatActive
              ? "按热度从高到低排序"
              : sort === "desc"
                ? "按热度从低到高排序"
                : "取消热度排序"
          }
        >
          热度 <HeatIcon aria-hidden="true" size={13} />
        </Link>
      )}
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
