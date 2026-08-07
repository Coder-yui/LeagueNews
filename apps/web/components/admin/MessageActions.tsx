"use client";

import { useState } from "react";
import { useEffect } from "react";
import { adminApi } from "@/lib/api";
import type { EventSummary } from "@/lib/types";
import { PIPELINE_STAGES, STAGE_LABELS, type PipelineStageName } from "./admin-utils";

export function MessageActions({ itemId }: { itemId: number }) {
  const [stage, setStage] = useState<PipelineStageName>("fact_classify"); const [busy, setBusy] = useState(false); const [message, setMessage] = useState<string | null>(null); const [events, setEvents] = useState<EventSummary[]>([]); const [eventId, setEventId] = useState<number | null>(null); const [role, setRole] = useState("primary");
  useEffect(() => { void adminApi<EventSummary[]>("/events?limit=200").then((rows) => { setEvents(rows); setEventId(rows[0]?.id ?? null); }).catch(() => setEvents([])); }, []);
  const rerun = async () => { setBusy(true); setMessage(null); try { await adminApi(`/pipeline/normalized-items/${itemId}/corrections`, { method: "POST", body: JSON.stringify({ restart_from_stage: stage === "ocr" ? "image_ocr" : stage, resume_mode: "manual", reason: `消息详情页从 ${stage} 修正` }) }); setMessage("已创建修正任务"); } catch (value) { setMessage(value instanceof Error ? value.message : "提交失败"); } finally { setBusy(false); } };
  const assign = async () => { if (!eventId) return; setBusy(true); setMessage(null); try { await adminApi(`/events/${eventId}/messages/${itemId}`, { method: "POST", body: JSON.stringify({ membership_role: role, evidence_stance: "supports", timeline_note: "消息详情页手动归属" }) }); setMessage("已写入事件归属并追加修订记录"); } catch (value) { setMessage(value instanceof Error ? value.message : "归属失败"); } finally { setBusy(false); } };
  return <div className="admin-action-panel"><label>从阶段重跑<select value={stage} onChange={(event) => setStage(event.target.value as PipelineStageName)}>{PIPELINE_STAGES.map((name) => <option value={name} key={name}>{STAGE_LABELS[name]}</option>)}</select></label><button onClick={() => void rerun()} disabled={busy}>{busy ? "提交中…" : "创建修正任务"}</button><label>目标事件<select value={eventId ?? ""} onChange={(event) => setEventId(Number(event.target.value))}>{events.map((event) => <option value={event.id} key={event.id}>{event.event_kind} · {event.title}</option>)}</select></label><label>归属角色<select value={role} onChange={(event) => setRole(event.target.value)}><option value="primary">primary</option><option value="component">component</option><option value="cross_ref">cross_ref</option></select></label><button onClick={() => void assign()} disabled={busy || !eventId}>手动归属事件</button>{message && <span>{message}</span>}</div>;
}
