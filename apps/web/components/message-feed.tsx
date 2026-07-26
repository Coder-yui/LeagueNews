import Image from "next/image";
import Link from "next/link";
import {
  ArrowUpRight,
  BadgeCheck,
  ImageIcon,
  ShieldQuestion,
} from "lucide-react";
import type { PublishedItem } from "@/lib/types";

const credibilityLabel: Record<string, string> = {
  official: "官方确认",
  corroborated: "多源印证",
  unverified: "尚未证实",
  rumor: "社区传闻",
};

function MessageCard({ item, index }: { item: PublishedItem; index: number }) {
  const image = item.translated_content_blocks.find(
    (block) => block.type === "image" && (block.storage_path || block.source_url),
  );
  const publishedAt = item.published_at ?? item.created_at;

  return (
    <article className="message-card">
      <div className="message-card-index">{String(index + 1).padStart(2, "0")}</div>
      <div className="message-card-copy">
        <div className="message-card-meta">
          <span>{item.category}</span>
          <time dateTime={publishedAt}>
            {new Date(publishedAt).toLocaleString("zh-CN", {
              month: "numeric",
              day: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </time>
          <span>{item.source_name}</span>
        </div>
        <Link href={`/messages/${item.id}`} className="message-card-title">
          <h3>{item.title}</h3>
        </Link>
        <p>{item.summary}</p>
        <div className="message-card-footer">
          <span className={`cred ${item.credibility}`}>
            {item.credibility === "official" ? (
              <BadgeCheck size={14} />
            ) : (
              <ShieldQuestion size={14} />
            )}
            {credibilityLabel[item.credibility] ?? item.credibility}
          </span>
          {item.entities.map((entity, entityIndex) => (
            <span className="entity" key={`${entity.name}-${entityIndex}`}>
              {entity.name}
            </span>
          ))}
          {item.media_extractions.length > 0 && (
            <span className="structured-badge">
              <ImageIcon size={13} /> 图片改动已结构化
            </span>
          )}
        </div>
        <Link href={`/messages/${item.id}`} className="message-card-link">
          查看完整消息 <ArrowUpRight size={14} />
        </Link>
      </div>
      {image && (
        <Link href={`/messages/${item.id}`} className="message-card-image" tabIndex={-1}>
          <Image
            src={image.storage_path ?? image.source_url ?? ""}
            alt={image.alt_text ?? image.caption ?? item.title}
            width={520}
            height={360}
            sizes="(max-width: 760px) 100vw, 320px"
          />
        </Link>
      )}
    </article>
  );
}

export function MessageFeed({ items }: { items: PublishedItem[] }) {
  if (!items.length) {
    return (
      <div className="message-empty">
        目前还没有完成全部人工审核的消息。
      </div>
    );
  }
  return (
    <div className="message-list">
      {items.map((item, index) => (
        <MessageCard item={item} index={index} key={item.id} />
      ))}
    </div>
  );
}
