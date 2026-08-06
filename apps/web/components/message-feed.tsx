"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  ImageIcon,
} from "lucide-react";
import { importanceLevel } from "@/lib/event-labels";
import type { PublishedItem } from "@/lib/types";

const MESSAGE_PAGE_SIZE = 10;

function MessageCard({ item, index }: { item: PublishedItem; index: number }) {
  const image = item.translated_content_blocks.find(
    (block) => block.type === "image" && (block.storage_path || block.source_url),
  );
  const publishedAt = item.published_at ?? item.created_at;
  const importance = Math.round(item.importance_score * 100);

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
          <span
            className={`importance-badge ${importanceLevel(item.importance_score)}`}
            title={`AI 评估的重要性得分：${importance}/100`}
          >
            重要性 {importance}
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
            unoptimized
          />
        </Link>
      )}
    </article>
  );
}

export function MessageFeed({ items }: { items: PublishedItem[] }) {
  const [page, setPage] = useState(1);
  const pageCount = Math.max(1, Math.ceil(items.length / MESSAGE_PAGE_SIZE));
  const visibleItems = items.slice(
    (page - 1) * MESSAGE_PAGE_SIZE,
    page * MESSAGE_PAGE_SIZE,
  );

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  if (!items.length) {
    return (
      <div className="message-empty">
        目前还没有完成全部人工审核的消息。
      </div>
    );
  }
  return (
    <>
      <div className="message-list">
        {visibleItems.map((item, index) => (
          <MessageCard
            item={item}
            index={(page - 1) * MESSAGE_PAGE_SIZE + index}
            key={item.id}
          />
        ))}
      </div>
      {pageCount > 1 && (
        <div className="public-pagination">
          <span>共 {items.length} 条消息</span>
          <div>
            <button
              type="button"
              disabled={page === 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
            >
              <ChevronLeft size={13} /> 上一页
            </button>
            <span>{page} / {pageCount}</span>
            <button
              type="button"
              disabled={page === pageCount}
              onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
            >
              下一页 <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}
