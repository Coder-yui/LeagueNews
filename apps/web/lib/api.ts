import type { Digest, EventDetail, EventSummary, PublishedItem } from "./types";

const apiUrl =
  process.env.INTERNAL_API_URL ??
  process.env.NEXT_PUBLIC_API_URL ??
  "http://localhost:8000/api/v1";

export async function getPublishedItems(): Promise<PublishedItem[]> {
  try {
    const response = await fetch(`${apiUrl}/normalized-items/published`, {
      next: { revalidate: 30 },
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as PublishedItem[];
  } catch {
    return [];
  }
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

export async function getEvents(): Promise<EventSummary[]> {
  try {
    const response = await fetch(`${apiUrl}/events`, {
      next: { revalidate: 30 },
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as EventSummary[];
  } catch {
    return [];
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

export async function getDigests(): Promise<Digest[]> {
  try {
    const response = await fetch(`${apiUrl}/digests`, {
      next: { revalidate: 60 },
    });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as Digest[];
  } catch {
    return [];
  }
}

export async function getDigest(id: number): Promise<Digest | null> {
  try {
    const response = await fetch(`${apiUrl}/digests/${id}`, {
      next: { revalidate: 60 },
    });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    return (await response.json()) as Digest;
  } catch {
    return null;
  }
}
