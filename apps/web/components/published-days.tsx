"use client";

import { ChevronsLeft, ChevronsRight, Ellipsis } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

type PublishedDayLink = {
  count: number;
  date: string;
  href: string;
};

const PAGE_SLOT_WIDTH = 62;
const MIN_PAGE_SLOTS = 3;
const INITIAL_PAGE_SLOTS = 8;

function paginateDays(days: PublishedDayLink[], slots: number) {
  const pages: Array<{ days: PublishedDayLink[]; hasNext: boolean; hasPrevious: boolean }> = [];
  let cursor = 0;
  while (cursor < days.length) {
    const hasPrevious = pages.length > 0;
    let capacity = slots - (hasPrevious ? 1 : 0);
    const hasNext = days.length - cursor > capacity;
    if (hasNext) capacity -= 1;
    const pageDays = days.slice(cursor, cursor + Math.max(1, capacity));
    pages.push({ days: pageDays, hasNext, hasPrevious });
    cursor += pageDays.length;
  }
  return pages.length ? pages : [{ days: [], hasNext: false, hasPrevious: false }];
}

export function PublishedDays({
  allHref,
  days,
  selectedDate,
}: {
  allHref: string;
  days: PublishedDayLink[];
  selectedDate?: string;
}) {
  const navRef = useRef<HTMLElement>(null);
  const [pageSlots, setPageSlots] = useState(INITIAL_PAGE_SLOTS);
  const [page, setPage] = useState(0);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const updatePageSlots = () => {
      const labelWidth = nav.querySelector(":scope > span")?.getBoundingClientRect().width ?? 0;
      const availableWidth = nav.clientWidth - labelWidth - PAGE_SLOT_WIDTH;
      const availableSlots = Math.floor(availableWidth / PAGE_SLOT_WIDTH);
      setPageSlots(Math.max(MIN_PAGE_SLOTS, availableSlots));
    };
    updatePageSlots();
    const observer = new ResizeObserver(updatePageSlots);
    observer.observe(nav);
    return () => observer.disconnect();
  }, []);

  const pages = useMemo(() => paginateDays(days, pageSlots), [days, pageSlots]);

  useEffect(() => {
    const selectedPage = pages.findIndex((entry) => entry.days.some((day) => day.date === selectedDate));
    if (selectedPage >= 0) setPage(selectedPage);
  }, [pages, selectedDate]);

  const currentPage = Math.min(page, pages.length - 1);
  const pageData = pages[currentPage];

  return (
    <nav className="published-days" aria-label="日期归档" ref={navRef}>
      <span>近期归档</span>
      <Link className={`published-days-all${!selectedDate ? " active" : ""}`} href={allHref}>全部</Link>
      <div className="published-days-page">
        {pageData.hasPrevious && (
          <button
            className="published-days-page-control previous"
            type="button"
            aria-label="查看上一组日期"
            title="上一组日期"
            onClick={() => setPage((value) => Math.max(0, value - 1))}
          >
            <Ellipsis className="dots" aria-hidden="true" size={18} />
            <ChevronsLeft className="arrow" aria-hidden="true" size={18} />
          </button>
        )}
        {pageData.days.map((day) => (
          <Link className={selectedDate === day.date ? "active" : ""} href={day.href} key={day.date}>
            <b>{day.date.slice(5)}</b>
            <small>{day.count}</small>
          </Link>
        ))}
        {pageData.hasNext && (
          <button
            className="published-days-page-control next"
            type="button"
            aria-label="查看下一组日期"
            title="下一组日期"
            onClick={() => setPage((value) => Math.min(pages.length - 1, value + 1))}
          >
            <Ellipsis className="dots" aria-hidden="true" size={18} />
            <ChevronsRight className="arrow" aria-hidden="true" size={18} />
          </button>
        )}
      </div>
    </nav>
  );
}
