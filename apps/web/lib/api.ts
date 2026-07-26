import { sampleEvents } from "./sample-data";
import type { NewsEvent } from "./types";

export async function getEvents(): Promise<{ events: NewsEvent[]; isDemo: boolean }> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";
  try {
    const response = await fetch(`${apiUrl}/events/feed`, { next: { revalidate: 60 } });
    if (!response.ok) throw new Error(`API returned ${response.status}`);
    const events = (await response.json()) as NewsEvent[];
    return { events: events.length ? events : sampleEvents, isDemo: events.length === 0 };
  } catch {
    return { events: sampleEvents, isDemo: true };
  }
}
