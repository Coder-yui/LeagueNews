"use client";

import Link from "next/link";
import { useState } from "react";
import type { RawAdminItem } from "@/lib/types";
import { adminApi } from "@/lib/api";
import { PIPELINE_STAGES, STAGE_LABELS, inferStages, type PipelineStageName } from "./admin-utils";
import { ImportanceDimensions } from "./ImportanceDimensions";

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

export function ItemDetailCard({ item, onChanged }: { item: RawAdminItem; onChanged?: () => void }) {
  const run = item.processing_runs[0];
  const context = run?.context ?? {};
  const [stage, setStage] = useState<PipelineStageName>("message_analysis");
  const [rawOpen, setRawOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stages = inferStages(item);
  const retryPending = item.current_pipeline_job_retry_pending;
  const canRetry = !retryPending && !item.normalized_item_id && run?.status === "failed";
  const canRestartFromBeginning = !retryPending && !item.normalized_item_id && (
    run?.status === "failed" ||
    item.processing_status === "failed" ||
    item.current_pipeline_job_status === "failed"
  );
  const importanceProposal = record(context.approved_importance_proposal);
  const importance = record(importanceProposal.importance_dimensions ?? context.importance_dimensions);

  const rerun = async () => {
    if (!item.normalized_item_id) { setError("该消息尚未生成 normalized item，不能从阶段重跑"); return; }
    setBusy(true); setError(null);
    try {
      await adminApi(`/pipeline/normalized-items/${item.normalized_item_id}/corrections`, { method: "POST", body: JSON.stringify({ restart_from_stage: stage, resume_mode: "automatic", reason: `管理台从 ${stage} 阶段重跑` }) });
      onChanged?.();
    } catch (value) { setError(value instanceof Error ? value.message : "重跑失败"); }
    finally { setBusy(false); }
  };

  const retry = async () => {
    if (!run) return;
    setBusy(true); setError(null);
    try {
      await adminApi(`/workflows/runs/${run.id}/retry`, {
        method: "POST",
        body: "{}",
      });
      onChanged?.();
    } catch (value) { setError(value instanceof Error ? value.message : "重试失败"); }
    finally { setBusy(false); }
  };

  const restartFromBeginning = async () => {
    if (!window.confirm("将从相关性开始重新执行完整处理链路，并创建新的 ProcessingRun。确定继续吗？")) return;
    setBusy(true); setError(null);
    try {
      await adminApi(`/raw-items/${item.id}/restart-from-beginning`, {
        method: "POST",
        body: "{}",
      });
      onChanged?.();
    } catch (value) { setError(value instanceof Error ? value.message : "从头重跑失败"); }
    finally { setBusy(false); }
  };

  return (
    <section className="admin-item-detail">
      <div className="admin-detail-intro"><div><span>原文摘要</span><p>{item.summary ?? item.content_blocks.find((block) => block.text)?.text ?? "暂无文本摘要"}</p></div><dl><div><dt>来源</dt><dd>{item.source_name} · {item.source_connector_type}</dd></div><div><dt>发布时间</dt><dd>{new Date(item.published_at ?? item.ingested_at).toLocaleString("zh-CN")}</dd></div><div><dt>处理 Run</dt><dd>{run ? `#${run.id} · ${run.status}` : "尚未启动"}</dd></div></dl></div>
      <div className="admin-stage-details">
        {stages.map((entry) => <div key={entry.name}><span className={`admin-mini-status ${entry.status}`}>{entry.status === "done" ? "✓" : entry.status === "failed" ? "×" : entry.status === "review" ? "Ⅱ" : "·"}</span><strong>{STAGE_LABELS[entry.name]}</strong><p>{entry.detail}</p></div>)}
      </div>
      <div className="admin-detail-breakdowns"><ImportanceDimensions scoreValue={item.importance_score} dimensions={importance} /></div>
      {item.normalized_item_id && <div className="admin-membership-strip"><span>发布投影</span><Link href={`/admin/messages/${item.normalized_item_id}`}>消息 #{item.normalized_item_id}</Link></div>}
      <div className="admin-inline-actions">
        {item.normalized_item_id && <><select aria-label="选择重跑阶段" value={stage} onChange={(event) => setStage(event.target.value as PipelineStageName)}>{PIPELINE_STAGES.map((name) => <option value={name} key={name}>{STAGE_LABELS[name]}</option>)}</select><button type="button" onClick={() => void rerun()} disabled={busy}>{busy ? "提交中…" : `重跑 ${STAGE_LABELS[stage]}`}</button></>}
        {canRetry && <button type="button" onClick={() => void retry()} disabled={busy}>{busy ? "提交中…" : "重试"}</button>}
        {canRestartFromBeginning && <button type="button" className="danger" onClick={() => void restartFromBeginning()} disabled={busy}>{busy ? "提交中…" : "从头重跑"}</button>}
        {retryPending && <span className="admin-badge">等待自动重试</span>}
        <button type="button" onClick={() => setRawOpen((value) => !value)}>{rawOpen ? "隐藏原始 JSON" : "查看原始 JSON"}</button>{item.canonical_url && <a href={item.canonical_url} target="_blank" rel="noreferrer">查看原文</a>}
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
      {rawOpen && <pre className="admin-raw-json">{JSON.stringify(item, null, 2)}</pre>}
    </section>
  );
}
