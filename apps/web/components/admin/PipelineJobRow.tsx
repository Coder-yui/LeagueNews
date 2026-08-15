"use client";

import { useState } from "react";
import { adminApi } from "@/lib/api";
import type { PipelineJob } from "@/lib/types";

export function PipelineJobRow({ job, onRecovered }: { job: PipelineJob; onRecovered: () => void }) {
  const [open, setOpen] = useState(false); const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  const retryPending = job.status === "failed" && job.next_attempt_at !== null;
  const retry = async () => { setBusy(true); setError(null); try { await adminApi(`/pipeline/jobs/${job.id}/recover`, { method: "POST", body: JSON.stringify({ restart_from_stage: job.current_stage, resume_mode: "automatic", reason: "管理台手动重试失败任务" }) }); onRecovered(); } catch (value) { setError(value instanceof Error ? value.message : "重试失败"); } finally { setBusy(false); } };
  return <><tr><td className="admin-number">#{job.id}</td><td className="admin-number">#{job.raw_item_id}</td><td><span className="admin-badge danger">{job.current_stage}</span></td><td>{job.error_message?.slice(0, 90) ?? "无错误摘要"}{error && <small className="admin-inline-error">{error}</small>}</td><td><div className="admin-row-actions">{retryPending ? <span className="admin-badge">等待自动重试</span> : <button type="button" onClick={() => void retry()} disabled={busy}>{busy ? "重试中…" : "重试"}</button>}<button type="button" onClick={() => setOpen((value) => !value)}>{open ? "收起" : "查看错误"}</button></div></td></tr>{open && <tr className="admin-expanded-row"><td colSpan={5}><pre className="admin-raw-json">{job.error_message ?? "无错误详情"}</pre></td></tr>}</>;
}
