export type PublicSortBy = "time" | "importance";
export type EventSortBy = PublicSortBy | "heat";
export type SortDirection = "asc" | "desc";
export type PublicSearchParams = Record<string, string | undefined>;

export function normalizePublicSortBy(value: string | undefined): PublicSortBy {
  return value === "importance" ? "importance" : "time";
}

export function normalizeEventSortBy(value: string | undefined): EventSortBy {
  if (value === "importance" || value === "heat") return value;
  return "time";
}

export function normalizeSortDirection(value: string | undefined): SortDirection {
  return value === "asc" ? "asc" : "desc";
}

export function normalizePage(value: string | undefined): number {
  const parsed = Number.parseInt(value ?? "1", 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 1;
}

export function publicListHref(
  pathname: string,
  current: PublicSearchParams,
  updates: Record<string, string | null>,
): string {
  const params = new URLSearchParams();
  Object.entries(current).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  Object.entries(updates).forEach(([key, value]) => {
    if (value === null) params.delete(key);
    else params.set(key, value);
  });
  const query = params.toString();
  return query ? `${pathname}?${query}` : pathname;
}
