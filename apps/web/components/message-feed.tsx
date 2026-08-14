import Image from "next/image";
import Link from "next/link";
import {
  ArrowUpRight,
} from "lucide-react";
import { contentFormLabel } from "@/lib/content-form-labels";
import { importanceLevel } from "@/lib/importance-labels";
import { formatPublicTime, publicLabel } from "@/lib/public-labels";
import type { PublishedItem } from "@/lib/types";

export function firstMessageImage(item: PublishedItem) {
  return item.translated_content_blocks.find(
    (block) => block.type === "image" && (block.storage_path || block.source_url),
  ) ?? item.original_content_blocks.find(
    (block) => block.type === "image" && (block.storage_path || block.source_url),
  );
}

function messageHref(item: PublishedItem, returnTo?: string, returnLabel?: string) {
  const params = new URLSearchParams();
  if (returnTo?.startsWith("/")) params.set("from", returnTo);
  if (returnLabel) params.set("fromLabel", returnLabel);
  const query = params.toString();
  return `/messages/${item.id}${query ? `?${query}` : ""}`;
}

export function MessageCard({ item, index, returnTo, returnLabel, compact = false }: { item: PublishedItem; index: number; returnTo?: string; returnLabel?: string; compact?: boolean }) {
  const image = firstMessageImage(item);
  const publishedAt = item.published_at ?? item.created_at;
  const importance = Math.round(item.importance_score * 100);
  const href = messageHref(item, returnTo, returnLabel);

  return (
    <article className={`message-card ${compact ? "compact" : ""}`}>
      <div className="message-card-index">{String(index + 1).padStart(2, "0")}</div>
      <div className="message-card-copy">
        <div className="message-card-meta">
          <span>{publicLabel(item.products[0] ?? "unknown")}</span>
          <span>{publicLabel(item.message_type)}</span>
          <time dateTime={publishedAt}>{formatPublicTime(publishedAt)}</time>
          <span className="content-form-badge">
            {contentFormLabel(item.content_form)}
          </span>
        </div>
        <Link href={href} className="message-card-title">
          <h3>{item.title}</h3>
        </Link>
        <p>{item.summary}</p>
        <div className="message-card-footer">
          <span
            className={`importance-badge ${importanceLevel(item.importance_score)}`}
            title={`AI 评估的重要性得分：${importance}/100`}
          >
            重要性 {importance}
          </span>
          <span className="source-indicator">{item.source_name}</span>
          {item.topics.slice(0, 2).map((topic) => (
            <span className="topic-badge" key={topic}>
              {publicLabel(topic)}
            </span>
          ))}
          {item.entities.slice(0, 2).map((entity, entityIndex) => (
            <span className="entity" key={`${entity.name}-${entityIndex}`}>
              {entity.name}
            </span>
          ))}
        </div>
        <Link href={href} className="message-card-link">
          阅读全文 <ArrowUpRight size={14} />
        </Link>
      </div>
      {image && (
        <Link href={href} className="message-card-image" tabIndex={-1}>
          <Image
            src={image.storage_path ?? image.source_url ?? ""}
            alt={image.alt_text ?? image.caption ?? item.title}
            width={520}
            height={360}
            sizes="(max-width: 760px) 100vw, 320px"
            unoptimized
          />
        </Link>
      )}
    </article>
  );
}

export function MessageFeed({
  items,
  startIndex = 0,
  returnTo,
  returnLabel,
  compact = false,
}: {
  items: PublishedItem[];
  startIndex?: number;
  returnTo?: string;
  returnLabel?: string;
  compact?: boolean;
}) {
  if (!items.length) {
    return (
      <div className="message-empty">
        当前条件下没有可公开阅读的消息。
      </div>
    );
  }
  return (
    <>
      <div className="message-list">
        {items.map((item, index) => (
          <MessageCard
            item={item}
            index={startIndex + index}
            returnTo={returnTo}
            returnLabel={returnLabel}
            compact={compact}
            key={item.id}
          />
        ))}
      </div>
    </>
  );
}
