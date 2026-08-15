"use client";

import { ArrowUp } from "lucide-react";

export function BackToTop() {
  return (
    <button
      className="public-back-to-top"
      type="button"
      aria-label="回到顶部"
      title="回到顶部"
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
    >
      <ArrowUp size={17} strokeWidth={2.4} />
    </button>
  );
}
