"use client";

import { useEffect, useState } from "react";
import { adminApi } from "@/lib/api";
import type { CollectionSchedule, Source } from "@/lib/types";

export function CollectionScheduleEditor({
  source,
  schedule,
  onClose,
  onSaved,
}: {
  source: Source;
  schedule?: CollectionSchedule;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [enabled, setEnabled] = useState(schedule?.enabled ?? false);
  const [intervalMinutes, setIntervalMinutes] = useState(schedule?.interval_minutes ?? 60);
  const [retryDelayMinutes, setRetryDelayMinutes] = useState(schedule?.retry_delay_minutes ?? 15);
  const [fetchLimit, setFetchLimit] = useState(schedule?.fetch_limit ?? 10);
  const [overlapMinutes, setOverlapMinutes] = useState(schedule?.overlap_minutes ?? 10);
  const [isOfficial, setIsOfficial] = useState(source.is_official);
  const [reliabilityScore, setReliabilityScore] = useState(source.reliability_score);
  const [busy, setBusy] = useState(false); const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setEnabled(schedule?.enabled ?? false); setIntervalMinutes(schedule?.interval_minutes ?? 60);
    setRetryDelayMinutes(schedule?.retry_delay_minutes ?? 15); setFetchLimit(schedule?.fetch_limit ?? 10);
    setOverlapMinutes(schedule?.overlap_minutes ?? 10); setError(null);
    setIsOfficial(source.is_official); setReliabilityScore(source.reliability_score);
  }, [schedule, source.id, source.is_official, source.reliability_score]);
  const save = async () => {
    setBusy(true); setError(null);
    try {
      await Promise.all([
        adminApi(`/collection-schedules/sources/${source.id}`, {
          method: "PUT",
          body: JSON.stringify({ enabled, interval_minutes: intervalMinutes, retry_delay_minutes: retryDelayMinutes, fetch_limit: fetchLimit, overlap_minutes: overlapMinutes, options: schedule?.options ?? {} }),
        }),
        adminApi(`/sources/${source.id}/reliability`, {
          method: "PATCH",
          body: JSON.stringify({ is_official: isOfficial, reliability_score: reliabilityScore }),
        }),
      ]);
      onSaved();
    } catch (value) { setError(value instanceof Error ? value.message : "保存失败"); }
    finally { setBusy(false); }
  };
  return <div className="admin-editor-backdrop" role="presentation" onMouseDown={onClose}>
    <section className="admin-schedule-editor" role="dialog" aria-modal="true" aria-labelledby="schedule-title" onMouseDown={(event) => event.stopPropagation()}>
      <header><div><span className="admin-eyebrow">COLLECTION PLAN</span><h2 id="schedule-title">修改采集计划</h2><p>{source.name} · {source.connector_type}</p></div><button type="button" aria-label="关闭" onClick={onClose}>×</button></header>
      <label className="admin-check"><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />启用自动采集计划</label>
      <label className="admin-check"><input type="checkbox" checked={isOfficial} onChange={(event) => setIsOfficial(event.target.checked)} />官方来源（仅直接原创内容构成官方证据）</label>
      <div className="admin-schedule-grid">
        <label>基础可靠性（0–1）<input type="number" min={0} max={1} step={0.05} value={reliabilityScore} onChange={(event) => setReliabilityScore(Number(event.target.value))} /></label>
        <label>采集间隔（分钟）<input type="number" min={5} max={10080} value={intervalMinutes} onChange={(event) => setIntervalMinutes(Number(event.target.value))} /></label>
        <label>失败重试（分钟）<input type="number" min={1} max={1440} value={retryDelayMinutes} onChange={(event) => setRetryDelayMinutes(Number(event.target.value))} /></label>
        <label>单次采集上限<input type="number" min={1} max={50} value={fetchLimit} onChange={(event) => setFetchLimit(Number(event.target.value))} /></label>
        <label>时间重叠（分钟）<input type="number" min={0} max={1440} value={overlapMinutes} onChange={(event) => setOverlapMinutes(Number(event.target.value))} /></label>
      </div>
      {error && <p className="admin-inline-error">{error}</p>}
      <footer><button type="button" onClick={onClose}>取消</button><button type="button" className="admin-primary-button" disabled={busy} onClick={() => void save()}>{busy ? "保存中…" : "保存采集计划"}</button></footer>
    </section>
  </div>;
}
