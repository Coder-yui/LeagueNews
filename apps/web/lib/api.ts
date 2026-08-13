import type { DailyReport, EventDetail, EventPage, PublishedItem, PublishedItemPage } from "./types";

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
  featured = false,
): Promise<PublishedItemPage> {
  const params = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (featured) params.set("featured", "true");
  const response = await fetch(`${apiUrl}/normalized-items/published-page?${params}`, {
    next: { revalidate: 30 },
  });
  return requireJson<PublishedItemPage>(response);
}

export async function getAllPublishedItems(featured = false): Promise<PublishedItemPage> {
  const pageSize = 100;
  const firstPage = await getPublishedItemsPage(pageSize, 0, featured);
  const offsets = Array.from(
    { length: Math.max(0, Math.ceil(firstPage.total / pageSize) - 1) },
    (_, index) => (index + 1) * pageSize,
  );
  const remainingPages = await Promise.all(
    offsets.map((offset) => getPublishedItemsPage(pageSize, offset, featured)),
  );
  return {
    ...firstPage,
    items: [firstPage, ...remainingPages].flatMap((page) => page.items),
  };
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
    next: { revalidate: 30 },
  });
  if (response.status === 404) return null;
  return requireJson<DailyReport>(response);
}

export async function getEventsPage(category?: string): Promise<EventPage> {
  const params = new URLSearchParams({ limit: "100" });
  if (category && category !== "all") params.set("category", category);
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
