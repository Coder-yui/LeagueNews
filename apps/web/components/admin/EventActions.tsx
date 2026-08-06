"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { adminApi } from "@/lib/api";

const lifecycleOptions = ["scheduled", "live", "developing", "unconfirmed", "confirmed", "completed", "resolved", "disputed", "expired_unconfirmed", "officially_refuted"];

export function EventActions({ eventId, lifecycle, summary }: { eventId: number; lifecycle: string; summary: string }) {
  const router = useRouter(); const [nextLifecycle, setNextLifecycle] = useState(lifecycle); const [nextSummary, setNextSummary] = useState(summary); const [busy, setBusy] = useState(false); const [message, setMessage] = useState<string | null>(null);
  const save = async () => { setBusy(true); setMessage(null); try { await adminApi(`/events/${eventId}`, { method: "PATCH", body: JSON.stringify({ lifecycle_status: nextLifecycle, summary: nextSummary, change_note: "管理台更新事件状态与摘要" }) }); setMessage("已保存并写入事件修订历史"); router.refresh(); } catch (value) { setMessage(value instanceof Error ? value.message : "保存失败"); } finally { setBusy(false); } };
  const reaggregate = async () => { setBusy(true); setMessage(null); try { await adminApi(`/events/${eventId}/reaggregate`, { method: "POST", body: "{}" }); setMessage("已从最新成员消息启动重新聚合"); } catch (value) { setMessage(value instanceof Error ? value.message : "重新聚合失败"); } finally { setBusy(false); } };
  return <div className="admin-event-actions"><label>生命周期<select value={nextLifecycle} onChange={(event) => setNextLifecycle(event.target.value)}>{lifecycleOptions.map((value) => <option key={value}>{value}</option>)}</select></label><label>事件摘要<textarea value={nextSummary} onChange={(event) => setNextSummary(event.target.value)} /></label><div><button onClick={() => void save()} disabled={busy}>{busy ? "保存中…" : "保存变更"}</button><button onClick={() => void reaggregate()} disabled={busy}>触发重新聚合</button>{message && <span>{message}</span>}</div></div>;
}
