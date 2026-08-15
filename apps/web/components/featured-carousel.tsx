"use client";

import { ArrowRight, ChevronsRight } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";
import { firstMessageImage } from "@/components/message-feed";
import { resolveImageSrc } from "@/lib/image-src";
import { formatPublicTime, publicLabel } from "@/lib/public-labels";
import type { PublishedItem } from "@/lib/types";

const ROTATION_INTERVAL_MS = 6500;

function itemHref(id: number) {
  return `/messages/${id}?from=%2F&fromLabel=${encodeURIComponent("返回首页")}`;
}

export function FeaturedCarousel({ items }: { items: PublishedItem[] }) {
  const [currentIndex, setCurrentIndex] = useState(0);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (paused || items.length <= 1) return;
    const timer = window.setTimeout(() => {
      setCurrentIndex((index) => (index + 1) % items.length);
    }, ROTATION_INTERVAL_MS);
    return () => window.clearTimeout(timer);
  }, [currentIndex, items.length, paused]);

  const goTo = (index: number) => setCurrentIndex(index % items.length);
  const remainingItems = items.filter((_, index) => index !== currentIndex);

  return (
    <>
      <article
        className="home-editorial-lead carousel"
        aria-roledescription="轮播"
        aria-label="精选消息"
        onPointerEnter={() => setPaused(true)}
        onPointerLeave={() => setPaused(false)}
        onFocusCapture={() => setPaused(true)}
        onBlurCapture={() => setPaused(false)}
      >
        {items.map((item, index) => {
          const image = firstMessageImage(item);
          const active = index === currentIndex;
          const href = itemHref(item.id);
          return (
            <div
              className={`home-editorial-slide${active ? " active" : ""}${image ? " has-image" : ""}`}
              aria-hidden={!active}
              inert={!active}
              key={item.id}
            >
              {image && (
                <div className="home-editorial-lead-image" aria-hidden="true">
                  <Image
                    src={resolveImageSrc(image.storage_path)}
                    alt=""
                    fill
                    sizes="(max-width: 760px) 100vw, 760px"
                    priority={index === 0}
                    unoptimized
                    referrerPolicy="no-referrer"
                  />
                </div>
              )}
              <div className="home-editorial-lead-overlay" />
              <div className="home-editorial-lead-copy">
                <div className="home-lead-meta">
                  <span>{publicLabel(item.products[0] ?? "unknown")}</span>
                  <span>{publicLabel(item.message_type)}</span>
                  <time dateTime={item.published_at ?? item.created_at}>{formatPublicTime(item.published_at ?? item.created_at)}</time>
                </div>
                <h2><Link href={href}>{item.title}</Link></h2>
                <p>{item.summary}</p>
                <Link className="ln-primary-link" href={href}>阅读完整消息 <ArrowRight size={16} /></Link>
              </div>
            </div>
          );
        })}

        {items.length > 1 && (
          <>
            <button
              className="home-carousel-next"
              type="button"
              aria-label="切换到下一条精选"
              title="下一条精选"
              onClick={() => goTo(currentIndex + 1)}
            >
              <ChevronsRight aria-hidden="true" size={23} strokeWidth={1.7} />
            </button>
            <div className="home-carousel-dots" aria-label="选择精选消息">
              {items.map((item, index) => (
                <button
                  className={index === currentIndex ? "active" : ""}
                  type="button"
                  aria-label={`切换到第 ${index + 1} 条精选：${item.title}`}
                  aria-current={index === currentIndex ? "true" : undefined}
                  title={item.title}
                  onClick={() => goTo(index)}
                  key={item.id}
                />
              ))}
            </div>
          </>
        )}
      </article>

      <aside className="home-briefs" aria-label="精选快报">
        <div className="home-briefs-head"><span>精选快报</span><b>{String(remainingItems.length).padStart(2, "0")}</b></div>
        {remainingItems.map((item, index) => (
          <article className="home-brief" key={item.id}>
            <span>{String(index + 1).padStart(2, "0")}</span>
            <div>
              <div className="home-brief-meta">
                <span>{publicLabel(item.products[0] ?? "unknown")}</span>
                <time dateTime={item.published_at ?? item.created_at}>{formatPublicTime(item.published_at ?? item.created_at)}</time>
              </div>
              <h3><Link href={itemHref(item.id)}>{item.title}</Link></h3>
              <small>{item.source_name}</small>
            </div>
          </article>
        ))}
      </aside>
    </>
  );
}
