import type { ReactNode } from "react";

export function SectionTitle({ eyebrow, title, aside }: { eyebrow: string; title: string; aside?: ReactNode }) {
  return (
    <header className="ln-section-title">
      <div><span>{eyebrow}</span><h2>{title}</h2></div>
      <i aria-hidden="true" />
      {aside && <div className="ln-section-aside">{aside}</div>}
    </header>
  );
}
