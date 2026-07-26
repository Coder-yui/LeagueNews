"use client";

import Image from "next/image";
import { useState } from "react";
import {
  ArrowUp,
  BadgeCheck,
  ExternalLink,
  Languages,
  ShieldQuestion,
} from "lucide-react";
import type {
  ContentBlock,
  PublishedItem,
  PublishedMediaExtraction,
} from "@/lib/types";

const credibilityLabel: Record<string, string> = {
  official: "官方确认",
  corroborated: "多源印证",
  unverified: "尚未证实",
  rumor: "社区传闻",
};

const sourceSectionLabels: Record<string, string> = {
  champion_buff: "CHAMPION BUFFS",
  champion_nerf: "CHAMPION NERFS",
  champion_adjustment: "CHAMPION ADJUSTMENTS",
  item_buff: "ITEM BUFFS",
  item_nerf: "ITEM NERFS",
  item_adjustment: "ITEM ADJUSTMENTS",
  rune_buff: "RUNE BUFFS",
  rune_nerf: "RUNE NERFS",
  rune_adjustment: "RUNE ADJUSTMENTS",
  system_buff: "SYSTEM BUFFS",
  system_nerf: "SYSTEM NERFS",
  system_adjustment: "SYSTEM ADJUSTMENTS",
  adjustment: "ADJUSTMENTS",
  other: "OTHER",
};

function BilingualPatchTable({
  extraction,
}: {
  extraction: PublishedMediaExtraction;
}) {
  const original = extraction.original_data;
  const translated = extraction.translated_data;
  const originalSections = original.sections ?? [];
  const translatedSections = translated.sections ?? [];

  return (
    <section className="bilingual-patch">
      <div className="bilingual-column-labels" aria-hidden="true">
        <span>EN · 图片原文</span>
        <span>中文 · 审核译文</span>
      </div>
      {originalSections.map((sourceSection, sectionIndex) => {
        const translatedSection = translatedSections[sectionIndex];
        return (
          <section className="bilingual-patch-section" key={`${sourceSection.section_type}-${sectionIndex}`}>
            <div className="bilingual-section-title">
              <strong>
                {sourceSectionLabels[sourceSection.section_type ?? ""] ?? sourceSection.label}
              </strong>
              <strong>{translatedSection?.label ?? sourceSection.label}</strong>
            </div>
            <div className="bilingual-table-scroll">
              <table>
                <tbody>
                  {(sourceSection.entries ?? []).map((sourceEntry, entryIndex) => {
                    const translatedEntry = translatedSection?.entries?.[entryIndex];
                    return (
                      <tr key={`${sourceEntry.target}-${entryIndex}`}>
                        <td>
                          <b>{sourceEntry.target}</b>
                          {(sourceEntry.changes?.length ? sourceEntry.changes : ["—"]).map(
                            (change, changeIndex) => (
                              <p key={changeIndex}>{change}</p>
                            ),
                          )}
                        </td>
                        <td>
                          <b>{translatedEntry?.target ?? "—"}</b>
                          {(translatedEntry?.changes?.length
                            ? translatedEntry.changes
                            : ["—"]
                          ).map((change, changeIndex) => (
                            <p key={changeIndex}>{change}</p>
                          ))}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>
        );
      })}
      {(original.warnings?.length ?? 0) > 0 && (
        <p className="bilingual-patch-warning">
          识别提示：{original.warnings?.join("；")}
        </p>
      )}
    </section>
  );
}

function ContentBlocks({
  blocks,
  extractions,
}: {
  blocks: ContentBlock[];
  extractions: PublishedMediaExtraction[];
}) {
  return (
    <div className="message-blocks">
      {blocks.map((block, index) => {
        const key = block.id ?? `${block.type}-${index}`;
        if (block.type === "image" && (block.storage_path || block.source_url)) {
          const extraction = extractions.find((item) => item.block_index === index);
          return (
            <div className="message-image-block" key={key}>
              <figure>
                <Image
                  src={block.storage_path ?? block.source_url ?? ""}
                  alt={block.alt_text ?? block.caption ?? "消息配图"}
                  width={1400}
                  height={1000}
                  sizes="(max-width: 900px) 100vw, 900px"
                  priority={index === 0}
                />
                {block.caption && <figcaption>{block.caption}</figcaption>}
              </figure>
              {extraction && <BilingualPatchTable extraction={extraction} />}
            </div>
          );
        }
        if (block.type === "embed" && block.source_url) {
          return (
            <aside className="message-embed" key={key}>
              <span>{block.text ?? "这部分媒体请在原始位置查看"}</span>
              <a href={block.source_url} target="_blank" rel="noreferrer">
                请在原始位置查看 <ExternalLink size={13} />
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
        if (block.type === "heading") return <h2 key={key}>{block.text}</h2>;
        if (block.type === "quote") return <blockquote key={key}>{block.text}</blockquote>;
        return <p key={key}>{block.text}</p>;
      })}
    </div>
  );
}

export function MessageDetail({ item }: { item: PublishedItem }) {
  const canTranslate = item.translation_status === "translated";
  const [view, setView] = useState<"translated" | "original">(
    canTranslate ? "translated" : "original",
  );
  const blocks =
    view === "translated" && canTranslate
      ? item.translated_content_blocks
      : item.original_content_blocks;
  const title =
    view === "translated" && canTranslate
      ? item.translated_title ?? item.title
      : item.original_title ?? item.title;

  return (
    <article className="message-detail">
      <header className="message-detail-head">
        <div className="message-detail-kicker">
          <span>{item.category}</span>
          <span>重要性 {Math.round(item.importance_score * 100)}</span>
          <span className={`cred ${item.credibility}`}>
            {item.credibility === "official" ? (
              <BadgeCheck size={14} />
            ) : (
              <ShieldQuestion size={14} />
            )}
            {credibilityLabel[item.credibility] ?? item.credibility}
          </span>
        </div>
        <h1>{title}</h1>
        <p>{item.summary}</p>
        <div className="message-detail-source">
          <div>
            <span>来源</span>
            <strong>{item.source_name}</strong>
            {item.author && <span>· {item.author}</span>}
            {item.published_at && (
              <time dateTime={item.published_at}>
                {new Date(item.published_at).toLocaleString("zh-CN")}
              </time>
            )}
          </div>
          {item.source_url && (
            <a href={item.source_url} target="_blank" rel="noreferrer">
              查看原始消息 <ExternalLink size={14} />
            </a>
          )}
        </div>
        {canTranslate && (
          <div className="message-language-toggle" aria-label="消息语言">
            <Languages size={15} />
            <button
              className={view === "translated" ? "active" : ""}
              type="button"
              onClick={() => setView("translated")}
            >
              中文译文
            </button>
            <button
              className={view === "original" ? "active" : ""}
              type="button"
              onClick={() => setView("original")}
            >
              原始内容
            </button>
          </div>
        )}
      </header>
      <ContentBlocks
        blocks={blocks}
        extractions={
          view === "translated" && canTranslate ? item.media_extractions : []
        }
      />
      <button
        className="message-back-to-top"
        type="button"
        aria-label="回到顶部"
        title="回到顶部"
        onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      >
        <ArrowUp size={17} strokeWidth={2.4} />
      </button>
    </article>
  );
}
