"use client";

import { useState } from "react";
import { adminApi } from "@/lib/api";
import {
  PIPELINE_STAGES,
  STAGE_LABELS,
  type PipelineStageName,
} from "./admin-utils";

export function MessageActions({ itemId }: { itemId: number }) {
  const [stage, setStage] = useState<PipelineStageName>("message_analysis");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const rerun = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await adminApi(`/pipeline/normalized-items/${itemId}/corrections`, {
        method: "POST",
        body: JSON.stringify({
          restart_from_stage: stage,
          resume_mode: "manual",
          reason: `消息详情页从 ${stage} 修正`,
        }),
      });
      setMessage("已创建修正任务");
    } catch (value) {
      setMessage(value instanceof Error ? value.message : "提交失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="admin-action-panel">
      <label>
        从阶段重跑
        <select
          value={stage}
          onChange={(event) => setStage(event.target.value as PipelineStageName)}
        >
          {PIPELINE_STAGES.map((name) => (
            <option value={name} key={name}>{STAGE_LABELS[name]}</option>
          ))}
        </select>
      </label>
      <button onClick={() => void rerun()} disabled={busy}>
        {busy ? "提交中..." : "创建修正任务"}
      </button>
      {message && <span>{message}</span>}
    </div>
  );
}
