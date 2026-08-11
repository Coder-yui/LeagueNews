import type { EventDetail, EventPage, PublishedItem, PublishedItemPage } from "./types";

export const apiUrl =
  process.env.INTERNAL_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000/api/v1";

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
): Promise<PublishedItemPage> {
  try {
    const params = new URLSearchParams({
      limit: String(limit),
      offset: String(offset),
    });
    const response = await fetch(`${apiUrl}/normalized-items/published-page?${params}`, {
      next: { revalidate: 30 },
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as PublishedItemPage;
  } catch {
    return {
      items: [],
      total: 0,
      product_options: [],
      message_type_options: [],
      topic_options: [],
    };
  }
}

export async function getAllPublishedItems(): Promise<PublishedItemPage> {
  const pageSize = 100;
  const firstPage = await getPublishedItemsPage(pageSize, 0);
  const offsets = Array.from(
    { length: Math.max(0, Math.ceil(firstPage.total / pageSize) - 1) },
    (_, index) => (index + 1) * pageSize,
  );
  const remainingPages = await Promise.all(
    offsets.map((offset) => getPublishedItemsPage(pageSize, offset)),
  );
  return {
    ...firstPage,
    items: [firstPage, ...remainingPages].flatMap((page) => page.items),
  };
}

export async function getPublishedItem(id: number): Promise<PublishedItem | null> {
  try {
    const response = await fetch(`${apiUrl}/normalized-items/${id}/published`, {
      next: { revalidate: 30 },
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as PublishedItem;
  } catch {
    return null;
  }
}

export async function getEventsPage(): Promise<EventPage> {
  try {
    const response = await fetch(`${apiUrl}/events?limit=100`, {
      next: { revalidate: 30 },
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as EventPage;
  } catch {
    return {
      items: [],
      total: 0,
      product_options: [],
      event_family_options: [],
      lifecycle_options: [],
      credibility_options: [],
    };
  }
}

export async function getEvent(id: number): Promise<EventDetail | null> {
  try {
    const response = await fetch(`${apiUrl}/events/${id}`, {
      next: { revalidate: 30 },
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as EventDetail;
  } catch {
    return null;
  }
}
