"use client";

export function PaginationControls({
  page,
  pageSize,
  total,
  sort,
  onPageChange,
  onPageSizeChange,
  onSortChange,
  showSort = true,
}: {
  page: number;
  pageSize: number;
  total: number;
  sort: "asc" | "desc";
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onSortChange: (sort: "asc" | "desc") => void;
  showSort?: boolean;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const start = total ? (page - 1) * pageSize + 1 : 0;
  const end = Math.min(page * pageSize, total);
  return (
    <div className="admin-pagination">
      <span>
        第 {start}–{end} 条，共 {total} 条
      </span>
      {showSort && (
        <label>
          时间排序
          <select
            value={sort}
            onChange={(event) =>
              onSortChange(event.target.value as "asc" | "desc")
            }
          >
            <option value="desc">最新优先</option>
            <option value="asc">最早优先</option>
          </select>
        </label>
      )}
      <label>
        每页
        <select
          value={pageSize}
          onChange={(event) => onPageSizeChange(Number(event.target.value))}
        >
          <option value={20}>20</option>
          <option value={25}>25</option>
          <option value={50}>50</option>
          <option value={100}>100</option>
        </select>
      </label>
      <div>
        <button
          type="button"
          disabled={page <= 1}
          onClick={() => onPageChange(page - 1)}
        >
          上一页
        </button>
        <b>
          {page} / {pages}
        </b>
        <button
          type="button"
          disabled={page >= pages}
          onClick={() => onPageChange(page + 1)}
        >
          下一页
        </button>
      </div>
    </div>
  );
}
