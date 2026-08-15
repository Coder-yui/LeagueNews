import type { ReactNode } from "react";

export function PublicPageMasthead({
  eyebrow,
  title,
  description,
  children,
}: {
  eyebrow: string;
  title: string;
  description: string;
  children?: ReactNode;
}) {
  return (
    <section className="public-page-masthead">
      <div className="public-frame">
        <p className="ln-eyebrow"><i /> {eyebrow}</p>
        <h1>{title}</h1>
        <p className="public-page-description">{description}</p>
        {children}
      </div>
    </section>
  );
}
