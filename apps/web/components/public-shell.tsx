import type { ReactNode } from "react";
import { SiteFooter } from "./site-footer";
import { SiteHeader } from "./site-header";

export function PublicShell({ children, className = "" }: { children: ReactNode; className?: string }) {
  return (
    <div className={`public-site ${className}`}>
      <SiteHeader />
      <main className="public-main">{children}</main>
      <div className="public-frame"><SiteFooter /></div>
    </div>
  );
}
