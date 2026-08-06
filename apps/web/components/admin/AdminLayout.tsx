import type { ReactNode } from "react";
import { SideNav } from "./SideNav";

export function AdminLayout({ children, reviewCount, failedJobs }: { children: ReactNode; reviewCount: number; failedJobs: number }) {
  return <div className="admin-app"><SideNav reviewCount={reviewCount} failedJobs={failedJobs} /><main className="admin-main">{children}</main></div>;
}
