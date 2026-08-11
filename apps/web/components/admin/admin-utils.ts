import type { PipelineJob, ProcessingRun, RawAdminItem } from "@/lib/types";

export const PIPELINE_STAGES = [
  "relevance",
  "image_ocr",
  "translation",
  "message_analysis",
  "importance",
] as const;

export type PipelineStageName = (typeof PIPELINE_STAGES)[number];
export type StageStatus = "pending" | "running" | "done" | "failed" | "review" | "skipped";
export type StageView = { name: PipelineStageName; status: StageStatus; detail?: string };

export const STAGE_LABELS: Record<PipelineStageName, string> = {
  relevance: "相关性",
  image_ocr: "图片 OCR",
  translation: "翻译",
  message_analysis: "消息分析",
  importance: "重要性",
};

export function canonicalStage(stage: string | null | undefined): PipelineStageName {
  return PIPELINE_STAGES.includes(stage as PipelineStageName)
    ? (stage as PipelineStageName)
    : "relevance";
}

function summarizeContext(context: Record<string, unknown>, stage: PipelineStageName): string {
  const contextKeys: Record<PipelineStageName, string[]> = {
    relevance: ["relevance_decision"],
    image_ocr: ["approved_media_extraction_ids"],
    translation: ["approved_translation_proposal", "translation"],
    message_analysis: ["approved_message_analysis_proposal", "message_analysis"],
    importance: ["approved_importance_proposal", "importance"],
  };
  const value = contextKeys[stage].map((key) => context[key]).find((entry) => entry !== undefined);
  if (typeof value === "string") return value.slice(0, 80);
  if (value && typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .slice(0, 2)
      .map(([key, entry]) => `${key}: ${String(entry)}`)
      .join(" · ")
      .slice(0, 100);
  }
  return "暂无阶段摘要";
}

export function inferStages(item: RawAdminItem, job?: PipelineJob): StageView[] {
  const run: ProcessingRun | undefined = item.processing_runs[0];
  const current = canonicalStage(job?.current_stage ?? item.current_pipeline_stage ?? run?.current_stage);
  const currentIndex = PIPELINE_STAGES.indexOf(current);
  const context = run?.context ?? {};
  if (run?.outcome === "not_relevant") {
    return PIPELINE_STAGES.map((name, index) => ({
      name,
      status: index === 0 ? "done" : "skipped",
      detail: summarizeContext(context, name),
    }));
  }
  const complete = (job?.status === "completed" || item.current_pipeline_job_status === "completed") &&
    ["approved", "completed", "analyzed"].includes(run?.status ?? item.processing_status);
  return PIPELINE_STAGES.map((name, index) => {
    let status: StageStatus = "pending";
    if (run?.status === "failed" || job?.status === "failed" || item.current_pipeline_job_status === "failed") {
      status = index < currentIndex ? "done" : index === currentIndex ? "failed" : "pending";
    } else if (complete || item.processing_status === "analyzed") status = "done";
    else if (run?.status === "awaiting_review") {
      status = index < currentIndex ? "done" : index === currentIndex ? "review" : "pending";
    } else if (run?.status === "running" || job?.status === "running") {
      status = index < currentIndex ? "done" : index === currentIndex ? "running" : "pending";
    } else if (job?.status === "queued") {
      status = index === currentIndex ? "running" : "pending";
    }
    return { name, status, detail: summarizeContext(context, name) };
  });
}

export function relativeTime(value: string | null | undefined): string {
  if (!value) return "未知";
  const delta = Date.now() - new Date(value).getTime();
  const minutes = Math.max(0, Math.floor(delta / 60_000));
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

export function pointScore(value: number | null | undefined): string {
  return typeof value === "number" ? String(Math.round(value * 100)) : "—";
}
