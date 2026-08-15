import { ArrowUpRight } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { formatPublicTime, publicLabel } from "@/lib/public-labels";
import type { EventCard as EventCardType } from "@/lib/types";

export function EventCard({ event, featured = false, returnTo }: { event: EventCardType; featured?: boolean; returnTo?: string }) {
  const params = new URLSearchParams();
  if (returnTo?.startsWith("/")) params.set("from", returnTo);
  const href = `/events/${event.id}${params.size ? `?${params}` : ""}`;
  return (
    <article className={`ln-event-card ${featured ? "featured" : ""}`}>
      {event.best_media_url && (
        <Link className="ln-event-image" href={href} tabIndex={-1}>
          <Image src={event.best_media_url} alt="" width={960} height={540} sizes={featured ? "(max-width: 760px) 100vw, 60vw" : "(max-width: 760px) 100vw, 360px"} unoptimized referrerPolicy="no-referrer" />
        </Link>
      )}
      <div className="ln-event-copy">
        <div className="ln-card-labels">
          <span>{publicLabel(event.category)}</span>
          <span>{publicLabel(event.lifecycle_status)}</span>
          <time dateTime={event.last_material_update_at ?? undefined}>{formatPublicTime(event.last_material_update_at)}</time>
        </div>
        <h3><Link href={href}>{event.title}</Link></h3>
        <p>{event.current_summary}</p>
        <div className="ln-event-metrics" aria-label="事件指标">
          <span><b>{Math.round(event.importance_score * 100)}</b>重要性</span>
          <span><b>{publicLabel(event.credibility_level)}</b>可信度</span>
          <span><b>{Math.round(event.heat_score * 100)}</b>热度</span>
          <span><b>{event.source_count}</b>信源</span>
        </div>
        <Link className="ln-text-link" href={href}>追踪事件 <ArrowUpRight size={14} /></Link>
      </div>
    </article>
  );
}
