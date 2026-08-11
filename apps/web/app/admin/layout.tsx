import type { ReactNode } from "react";
import { AdminLayout } from "@/components/admin/AdminLayout";
import { adminApi } from "@/lib/api";
import type { PipelineJob, ReviewTask } from "@/lib/types";

export const dynamic = "force-dynamic";

export default async function Layout({ children }: { children: ReactNode }) {
  const [messageReviews, failedJobs] = await Promise.all([
    adminApi<ReviewTask[]>("/workflows/reviews?status=pending").catch(() => []),
    adminApi<PipelineJob[]>("/pipeline/jobs?status=failed").catch(() => []),
  ]);
  return <AdminLayout reviewCount={messageReviews.length} failedJobs={failedJobs.length}>{children}</AdminLayout>;
}
