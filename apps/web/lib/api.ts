import type { PublishedItem } from "./types";

const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

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
