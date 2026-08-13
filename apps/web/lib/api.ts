import type {
  DailyReport,
  EventDetail,
  EventPage,
  PublishedDayList,
  PublishedItem,
  PublishedItemPage,
} from "./types";
import type { PublicSortBy, SortDirection } from "./public-list";

export const apiUrl =
  process.env.INTERNAL_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000/api/v1";

async function requireJson<T>(response: Response): Promise<T> {
  if (!response.ok) throw new Error(`API returned ${response.status}`);
  return (await response.json()) as T;
}

export async function adminApi<T>(path: string, options?: RequestInit): Promise<T> {
  const baseUrl = typeof window === "undefined" ? apiUrl : "/api/v1";
  const response = await fetch(`${baseUrl}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    cache: options?.method && options.method !== "GET" ? "no-store" : options?.cache,
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getPublishedItemsPage(
  limit: number,
  offset: number,
  filters: {
    date?: string;
    featured?: boolean;
    product?: string;
    sortBy: PublicSortBy;
    sort: SortDirection;
    timezone?: string;
  },
): Promise<PublishedItemPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (filters.featured) params.set("featured", "true");
  if (filters.product) params.set("product", filters.product);
  if (filters.date) params.set("date", filters.date);
  if (filters.timezone) params.set("timezone", filters.timezone);
  params.set("sort_by", filters.sortBy);
  params.set("sort", filters.sort);
  const response = await fetch(`${apiUrl}/normalized-items/published-page?${params}`, {
    next: { revalidate: 30 },
  });
  return requireJson<PublishedItemPage>(response);
}

export async function getPublishedDays(
  limit = 30,
  filters: { featured?: boolean; product?: string; timezone?: string } = {},
): Promise<PublishedDayList> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (filters.featured) params.set("featured", "true");
  if (filters.product) params.set("product", filters.product);
  if (filters.timezone) params.set("timezone", filters.timezone);
  const response = await fetch(`${apiUrl}/normalized-items/published-days?${params}`, {
    next: { revalidate: 30 },
  });
  return requireJson<PublishedDayList>(response);
}

export async function getPublishedItem(id: number): Promise<PublishedItem | null> {
  const response = await fetch(`${apiUrl}/normalized-items/${id}/published`, {
    next: { revalidate: 30 },
  });
  if (response.status === 404) return null;
  return requireJson<PublishedItem>(response);
}

export async function getDailyReport(reportDate: string): Promise<DailyReport | null> {
  const response = await fetch(`${apiUrl}/reports/daily/${reportDate}`, {
    cache: "no-store",
  });
  if (response.status === 404) return null;
  return requireJson<DailyReport>(response);
}

export async function getEventsPage(
  limit: number,
  offset: number,
  filters: {
    category?: string;
    sortBy: PublicSortBy;
    sort: SortDirection;
  },
): Promise<EventPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    sort_by: filters.sortBy,
    sort: filters.sort,
  });
  if (filters.category && filters.category !== "all") {
    params.set("category", filters.category);
  }
  const response = await fetch(`${apiUrl}/events?${params}`, {
    next: { revalidate: 30 },
  });
  return requireJson<EventPage>(response);
}

export async function getEvent(id: number): Promise<EventDetail | null> {
  const response = await fetch(`${apiUrl}/events/${id}`, {
    next: { revalidate: 30 },
  });
  if (response.status === 404) return null;
  return requireJson<EventDetail>(response);
}
