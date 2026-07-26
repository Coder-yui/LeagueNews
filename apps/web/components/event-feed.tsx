"use client";

import Image from "next/image";
import { useState } from "react";
import {
  BadgeCheck,
  ChevronDown,
  ExternalLink,
  Languages,
  ShieldQuestion,
} from "lucide-react";
import type { ContentBlock, MediaExtraction, NewsEvent, PatchPreviewData } from "@/lib/types";

const credibilityLabel: Record<string, string> = {
  official: "官方确认",
  corroborated: "多源印证",
  unverified: "尚未证实",
  rumor: "社区传闻",
};

function PatchPreviewTable({
  data,
  confidence,
}: {
  data: PatchPreviewData;
  confidence: number | null;
}) {
  return (
    <section className="patch-extraction">
      <div className="patch-extraction-head">
        <div>
          <span>OCR + AI 结构化提取</span>
          <h5>{data.title}</h5>
        </div>
        {confidence !== null && <strong>OCR {Math.round(confidence * 100)}%</strong>}
      </div>
      {data.sections.map((section, sectionIndex) => (
        <div className="patch-section" key={`${section.section_type}-${sectionIndex}`}>
          <h6>{section.label}</h6>
          {section.entries.map((entry, entryIndex) => (
            <div className="patch-entry" key={`${entry.target}-${entryIndex}`}>
              <b>{entry.target}</b>
              <div className="patch-changes">
                {entry.changes.map((change, changeIndex) => (
                  <div className="patch-change" key={`${change.attribute}-${changeIndex}`}>
                    <span>{change.attribute}</span>
                    <div>
                      {change.before !== null && <del>{change.before}</del>}
                      {change.before !== null && change.after !== null && <i>→</i>}
                      {change.after !== null ? <ins>{change.after}</ins> : <em>{change.raw_text}</em>}
                    </div>
                    <small>{Math.round(change.confidence * 100)}%</small>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
      {data.warnings.length > 0 && (
        <div className="patch-warnings">识别提示：{data.warnings.join("；")}</div>
      )}
      <p className="patch-disclaimer">AI 从图片提取，数值请以原图为准。</p>
    </section>
  );
}

function ArticleBlocks({
  blocks,
  extractions,
}: {
  blocks: ContentBlock[];
  extractions: MediaExtraction[];
}) {
  return (
    <div className="article-blocks">
      {blocks.map((block, index) => {
        const key = block.id ?? `${block.type}-${index}`;
        if (block.type === "image" && (block.storage_path || block.source_url)) {
          const extraction = extractions.find(
            (item) => item.task_type === "patch_preview" && item.storage_path === block.storage_path,
          );
          return (
            <div key={key}>
              <figure className="article-media">
                <Image
                  src={block.storage_path ?? block.source_url ?? ""}
                  alt={block.alt_text ?? block.caption ?? "资讯配图"}
                  width={1400}
                  height={800}
                  sizes="(max-width: 760px) 100vw, 860px"
                />
                {block.caption && <figcaption>{block.caption}</figcaption>}
              </figure>
              {extraction && (
                <PatchPreviewTable
                  data={extraction.structured_data}
                  confidence={extraction.confidence}
                />
              )}
            </div>
          );
        }
        if (block.type === "embed" && block.source_url) {
          return (
            <aside className="article-embed" key={key}>
              <span>{block.text ?? "媒体内容"}，请前往原文查看</span>
              <a href={block.source_url} target="_blank" rel="noreferrer">
                打开原内容 <ExternalLink size={12} />
              </a>
            </aside>
          );
        }
        if (block.type === "list" && block.items?.length) {
          const ListTag = block.ordered ? "ol" : "ul";
          return (
            <ListTag key={key}>
              {block.items.map((item, itemIndex) => (
                <li key={`${key}-${itemIndex}`}>{item}</li>
              ))}
            </ListTag>
          );
        }
        if (!block.text) return null;
        if (block.type === "heading") return <h4 key={key}>{block.text}</h4>;
        if (block.type === "quote") return <blockquote key={key}>{block.text}</blockquote>;
        return <p key={key}>{block.text}</p>;
      })}
    </div>
  );
}

function EventCard({ event, index }: { event: NewsEvent; index: number }) {
  const primary = event.items[0];
  const canTranslate = primary?.translation_status === "translated";
  const [expanded, setExpanded] = useState(false);
  const [view, setView] = useState<"translated" | "original">(
    canTranslate ? "translated" : "original",
  );
  const blocks =
    view === "translated" && canTranslate
      ? primary.translated_content_blocks
      : primary?.original_content_blocks ?? [];
  const displayTitle =
    view === "translated" && canTranslate
      ? primary.translated_title ?? event.title
      : primary?.original_title ?? event.title;
  const sourceLink = primary?.source_url ?? primary?.source_base_url;

  return (
    <article className={`event-card ${expanded ? "expanded" : ""}`}>
      <div className="event-index">{String(index + 1).padStart(2, "0")}</div>
      <div className="event-content">
        <div className="event-topline">
          <span>{event.category}</span>
          <time>{Math.round(event.importance_score * 100)} / 100</time>
        </div>
        <h3>{displayTitle}</h3>
        <p>{event.summary}</p>
        <div className="event-footer">
          <span className={`cred ${event.credibility}`}>
            {event.credibility === "official" ? (
              <BadgeCheck size={14} />
            ) : (
              <ShieldQuestion size={14} />
            )}
            {credibilityLabel[event.credibility] ?? event.credibility}
          </span>
          {event.entities.map((entity, entityIndex) => (
            <span className="entity" key={`${entity.name}-${entityIndex}`}>
              {entity.name}
            </span>
          ))}
        </div>

        {primary && (
          <div className="source-line">
            <span>来源</span>
            {sourceLink ? (
              <a href={sourceLink} target="_blank" rel="noreferrer">
                {primary.source_name} <ExternalLink size={12} />
              </a>
            ) : (
              <strong>{primary.source_name}</strong>
            )}
            {primary.author && <span>· {primary.author}</span>}
          </div>
        )}

        {primary && (
          <div className="reader-actions">
            <button type="button" onClick={() => setExpanded((value) => !value)}>
              {expanded ? "收起全文" : "阅读全文"}
              <ChevronDown className={expanded ? "rotated" : ""} size={16} />
            </button>
            {canTranslate && expanded && (
              <div className="language-toggle" aria-label="正文语言">
                <Languages size={15} />
                <button
                  className={view === "translated" ? "active" : ""}
                  type="button"
                  onClick={() => setView("translated")}
                >
                  中文
                </button>
                <button
                  className={view === "original" ? "active" : ""}
                  type="button"
                  onClick={() => setView("original")}
                >
                  原文
                </button>
              </div>
            )}
          </div>
        )}

        {expanded && primary && (
          <div className="article-reader">
            <div className="reader-label">
              {view === "translated" && canTranslate ? "AI 中文翻译" : "信源原文"}
            </div>
            <ArticleBlocks blocks={blocks} extractions={primary.media_extractions} />
          </div>
        )}
      </div>
    </article>
  );
}

export function EventFeed({ events }: { events: NewsEvent[] }) {
  return (
    <div className="event-list">
      {events.map((event, index) => (
        <EventCard event={event} index={index} key={event.id} />
      ))}
    </div>
  );
}
