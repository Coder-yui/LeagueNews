"use client";

import Image from "next/image";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  BookOpenCheck,
  Check,
  ChevronLeft,
  ChevronRight,
  FileClock,
  LoaderCircle,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  ScanText,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";

type JsonObject = Record<string, unknown>;

type RawItem = {
  id: number;
  source_id: number;
  display_title: string | null;
  author_name: string | null;
  canonical_url: string | null;
  processing_status: string;
  published_at: string | null;
  ingested_at: string;
};

type Source = {
  id: number;
  name: string;
  connector_type: string;
  is_active: boolean;
};

type CollectionSchedule = {
  id: number;
  source_id: number;
  source_name: string;
  connector_type: string;
  enabled: boolean;
  interval_minutes: number;
  retry_delay_minutes: number;
  fetch_limit: number;
  options: JsonObject;
  next_run_at: string | null;
  run_requested_at: string | null;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_success_at: string | null;
  last_connector_run_id: number | null;
  last_status: string;
  last_error: string | null;
  lease_expires_at: string | null;
};

type ConnectorRun = {
  id: number;
  source_id: number;
  connector_type: string;
  status: string;
  discovered_count: number;
  created_count: number;
  revised_count: number;
  skipped_count: number;
  error_message: string | null;
  started_at: string;
  finished_at: string | null;
};

type ProcessingRun = {
  id: number;
  raw_item_id: number;
  status: string;
  current_stage: string;
};

type ReviewTask = {
  id: number;
  processing_run_id: number;
  stage: string;
  proposal: JsonObject;
};

type NormalizedItem = {
  id: number;
  raw_item_id: number;
  normalized_title: string;
  summary: string;
  category: string;
  current_revision: number;
  publication_status: string;
};

type PipelineJob = {
  id: number;
  raw_item_id: number;
  status: string;
  current_stage: string;
  attempts: number;
  error_message: string | null;
  last_checkpoint_id: number | null;
};

type PipelineCorrection = {
  id: number;
  raw_item_id: number;
  normalized_item_id: number | null;
  restart_from_stage: string;
  resume_mode: string;
  reason: string;
  status: string;
};

type EventAggregationRun = {
  id: number;
  normalized_item_id: number;
  status: string;
};

type EventReviewTask = {
  id: number;
  event_aggregation_run_id: number;
  proposal: JsonObject;
};

type KnowledgeRule = {
  id: number;
  knowledge_type: string;
  scope: string;
  rule_text: string;
  is_active: boolean;
};

type GlossaryTerm = {
  id: number;
  source_term: string;
  preferred_translation: string;
  is_active: boolean;
};

const knowledgeTypes = [
  ["relevance", "相关性"],
  ["analysis", "内容分析"],
  ["translation", "翻译"],
  ["event_aggregation", "事件聚合"],
] as const;

const KNOWLEDGE_PAGE_SIZE = 8;
const GLOSSARY_PAGE_SIZE = 12;
const ITEM_PAGE_SIZE = 10;
const REVIEW_PAGE_SIZE = 5;
const EVENT_PAGE_SIZE = 10;
const AUTOMATION_LOG_PAGE_SIZE = 10;

type TimeSort = "desc" | "asc";

type OCRParameters = {
  scale: number;
  grayscale: boolean;
  contrast: number;
  sharpness: number;
  text_score: number | null;
  box_thresh: number | null;
  unclip_ratio: number | null;
  use_cls: boolean;
  divider_x_ratio: number | null;
  line_brightness: number;
  line_coverage: number;
};

type OCRAsset = {
  media_asset_id: number;
  raw_item_id: number;
  raw_title: string | null;
  published_at: string | null;
  block_index: number;
  storage_path: string;
  source_url: string | null;
  width: number | null;
  height: number | null;
};

type OCRLine = {
  index: number;
  text: string;
  confidence: number | null;
  box: number[][] | null;
};

type PatchTableRecord = {
  target: string;
  raw_changes: string[];
  bbox: number[];
  ocr_confidence: number;
};

type PatchTableSection = {
  section_type: string;
  label: string;
  records: PatchTableRecord[];
};

type PatchTableData = {
  preview_kind?: "preview" | "full_preview";
  divider_x?: number | null;
  structure_confidence?: number;
  warnings?: string[];
  sections?: PatchTableSection[];
  boundaries?: number[];
};

type OCRTestRun = {
  id: number;
  media_asset_id: number;
  profile_name: string;
  parameters: OCRParameters;
  status: string;
  raw_text: string;
  lines: OCRLine[];
  confidence: number;
  source_width: number;
  source_height: number;
  processed_width: number;
  processed_height: number;
  overlay_path: string | null;
  table_overlay_path: string | null;
  table_data: PatchTableData;
  structure_confidence: number | null;
  engine: string;
  created_at: string;
};

type MediaExtraction = {
  id: number;
  media_asset_id: number;
  provider: string;
  schema_version: string;
  ocr_lines: OCRLine[];
  structured_data: JsonObject;
  processing_config: {
    table_data?: PatchTableData;
    manual_correction?: JsonObject;
  };
  confidence: number | null;
};

type OCRCorrectionDraft = {
  extractionId: number;
  tableData: PatchTableData;
  dirty: boolean;
  invalid: boolean;
};

type GlossaryCorrectionDraft = {
  id: number;
  source_term: string;
  preferred_translation: string;
};

type OCRProfile = {
  id: number;
  name: string;
  parameters: OCRParameters;
  source_test_run_id: number | null;
  is_active: boolean;
};

type Tab = "items" | "reviews" | "events" | "automation" | "ocr" | "knowledge";

const apiBase = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000/api/v1";

const stageLabels: Record<string, string> = {
  relevance: "相关性审核",
  image_ocr: "图片 OCR 审核",
  item_analysis: "分析与摘要审核",
  translation: "翻译与术语审核",
};

const connectorLabels: Record<string, string> = {
  manual: "手动录入",
  riot_official: "拳头官网",
  tencent_lol: "腾讯英雄联盟官网",
  x_twitter: "X",
  weibo: "微博",
  baidu_tieba: "百度贴吧",
};

function latestRunsByRawItem(runs: ProcessingRun[]): ProcessingRun[] {
  const latest = new Map<number, ProcessingRun>();
  for (const run of runs) {
    const current = latest.get(run.raw_item_id);
    if (!current || run.id > current.id) latest.set(run.raw_item_id, run);
  }
  return Array.from(latest.values());
}

function latestPipelineJobsByRawItem(jobs: PipelineJob[]): PipelineJob[] {
  const latest = new Map<number, PipelineJob>();
  for (const job of jobs) {
    const current = latest.get(job.raw_item_id);
    if (!current || job.id > current.id) latest.set(job.raw_item_id, job);
  }
  return Array.from(latest.values());
}

function rawItemTime(item: RawItem | undefined): number {
  if (!item) return 0;
  return new Date(item.published_at ?? item.ingested_at).getTime();
}

function sortedByMessageTime<T>(
  items: T[],
  rawItemFor: (item: T) => RawItem | undefined,
  direction: TimeSort,
): T[] {
  const multiplier = direction === "asc" ? 1 : -1;
  return [...items].sort((left, right) => {
    const timeDifference =
      rawItemTime(rawItemFor(left)) - rawItemTime(rawItemFor(right));
    if (timeDifference) return timeDifference * multiplier;
    return 0;
  });
}

function pageItems<T>(items: T[], page: number, pageSize: number): T[] {
  return items.slice((page - 1) * pageSize, page * pageSize);
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(payload?.detail ?? `API returned ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function AdminConsole() {
  const [tab, setTab] = useState<Tab>("items");
  const [rawItems, setRawItems] = useState<RawItem[]>([]);
  const [sources, setSources] = useState<Source[]>([]);
  const [runs, setRuns] = useState<ProcessingRun[]>([]);
  const [reviews, setReviews] = useState<ReviewTask[]>([]);
  const [normalizedItems, setNormalizedItems] = useState<NormalizedItem[]>([]);
  const [eventRuns, setEventRuns] = useState<EventAggregationRun[]>([]);
  const [eventReviews, setEventReviews] = useState<EventReviewTask[]>([]);
  const [pipelineJobs, setPipelineJobs] = useState<PipelineJob[]>([]);
  const [corrections, setCorrections] = useState<PipelineCorrection[]>([]);
  const [collectionSchedules, setCollectionSchedules] = useState<CollectionSchedule[]>([]);
  const [connectorRuns, setConnectorRuns] = useState<ConnectorRun[]>([]);
  const [rules, setRules] = useState<KnowledgeRule[]>([]);
  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [ocrAssets, setOcrAssets] = useState<OCRAsset[]>([]);
  const [ocrRuns, setOcrRuns] = useState<OCRTestRun[]>([]);
  const [ocrProfiles, setOcrProfiles] = useState<OCRProfile[]>([]);
  const [mediaExtractions, setMediaExtractions] = useState<MediaExtraction[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [
        raw,
        sourceRows,
        runRows,
        reviewRows,
        normalizedRows,
        eventRunRows,
        eventReviewRows,
        ruleRows,
        termRows,
        ocrAssetRows,
        ocrRunRows,
        ocrProfileRows,
        extractionRows,
        pipelineJobRows,
        correctionRows,
        collectionScheduleRows,
        connectorRunRows,
      ] =
        await Promise.all([
          api<RawItem[]>("/raw-items"),
          api<Source[]>("/sources"),
          api<ProcessingRun[]>("/workflows/runs"),
          api<ReviewTask[]>("/workflows/reviews?status=pending"),
          api<NormalizedItem[]>("/normalized-items"),
          api<EventAggregationRun[]>("/event-workflows/runs"),
          api<EventReviewTask[]>("/event-workflows/reviews?status=pending"),
          api<KnowledgeRule[]>("/knowledge/rules"),
          api<GlossaryTerm[]>("/knowledge/glossary"),
          api<OCRAsset[]>("/ocr-lab/assets"),
          api<OCRTestRun[]>("/ocr-lab/runs"),
          api<OCRProfile[]>("/ocr-lab/profiles"),
          api<MediaExtraction[]>("/media-assets/extractions"),
          api<PipelineJob[]>("/pipeline/jobs"),
          api<PipelineCorrection[]>("/pipeline/corrections"),
          api<CollectionSchedule[]>("/collection-schedules"),
          api<ConnectorRun[]>("/connectors/runs"),
        ]);
      setRawItems(raw);
      setSources(sourceRows);
      setRuns(runRows);
      setReviews(reviewRows);
      setNormalizedItems(normalizedRows);
      setEventRuns(eventRunRows);
      setEventReviews(eventReviewRows);
      setRules(ruleRows);
      setTerms(termRows);
      setOcrAssets(ocrAssetRows);
      setOcrRuns(ocrRunRows);
      setOcrProfiles(ocrProfileRows);
      setMediaExtractions(extractionRows);
      setPipelineJobs(pipelineJobRows);
      setCorrections(correctionRows);
      setCollectionSchedules(collectionScheduleRows);
      setConnectorRuns(connectorRunRows);
      setMessage(null);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "加载失败");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const act = async (key: string, action: () => Promise<unknown>, success: string) => {
    setBusy(key);
    setMessage(null);
    try {
      await action();
      setMessage(success);
      await refresh();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  const counts = useMemo(
    () => ({
      pending: rawItems.filter((item) => item.processing_status === "pending").length,
      reviews: reviews.length,
      eventReviews: eventReviews.length,
      retry: latestRunsByRawItem(runs)
        .filter((run) => ["failed", "rejected"].includes(run.status)).length,
      knowledge: rules.filter((rule) => rule.is_active).length + terms.filter((term) => term.is_active).length,
      failedJobs: latestPipelineJobsByRawItem(pipelineJobs)
        .filter((job) => job.status === "failed").length,
    }),
    [rawItems, reviews, eventReviews, runs, rules, terms, pipelineJobs],
  );

  return (
    <>
      <section className="admin-stats">
        <div><span>等待开始</span><strong>{counts.pending}</strong></div>
        <div><span>待人工审核</span><strong>{counts.reviews + counts.eventReviews}</strong></div>
        <div><span>等待重试</span><strong>{counts.retry}</strong></div>
        <div><span>自动管线失败</span><strong>{counts.failedJobs}</strong></div>
        <div><span>生效知识</span><strong>{counts.knowledge}</strong></div>
      </section>

      <div className="admin-tabs">
        {([
          ["items", "单条处理", FileClock],
          ["reviews", "审核中心", Check],
          ["events", "事件聚合", Sparkles],
          ["automation", "自动化与撤回", Activity],
          ["ocr", "OCR 测试台", ScanText],
          ["knowledge", "知识与术语", BookOpenCheck],
        ] as const).map(([value, label, Icon]) => (
          <button
            className={tab === value ? "active" : ""}
            key={value}
            type="button"
            onClick={() => setTab(value)}
          >
            <Icon size={15} /> {label}
          </button>
        ))}
        <button className="refresh-button" type="button" onClick={() => void refresh()}>
          <RefreshCw size={14} /> 刷新
        </button>
      </div>

      {message && <div className="admin-message">{message}</div>}

      {tab === "items" && (
        <ItemsPanel
          rawItems={rawItems}
          runs={runs}
          busy={busy}
          act={act}
        />
      )}
      {tab === "reviews" && (
        <ReviewCenterPanel
          reviews={reviews}
          eventReviews={eventReviews}
          runs={runs}
          eventRuns={eventRuns}
          normalizedItems={normalizedItems}
          rawItems={rawItems}
          sources={sources}
          mediaExtractions={mediaExtractions}
          ocrAssets={ocrAssets}
          busy={busy}
          act={act}
        />
      )}
      {tab === "ocr" && (
        <OCRLabPanel
          assets={ocrAssets}
          runs={ocrRuns}
          profiles={ocrProfiles}
          busy={busy}
          act={act}
        />
      )}
      {tab === "automation" && (
        <PipelinePanel
          sources={sources}
          schedules={collectionSchedules}
          connectorRuns={connectorRuns}
          items={normalizedItems}
          jobs={pipelineJobs}
          corrections={corrections}
          busy={busy}
          act={act}
        />
      )}
      {tab === "events" && (
        <EventAggregationPanel
          items={normalizedItems}
          runs={eventRuns}
          rawItems={rawItems}
          busy={busy}
          act={act}
        />
      )}
      {tab === "knowledge" && (
        <KnowledgePanel rules={rules} terms={terms} busy={busy} act={act} />
      )}
    </>
  );
}

function SourceScheduleCard({
  source,
  schedule,
  busy,
  act,
}: {
  source: Source;
  schedule: CollectionSchedule | undefined;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [enabled, setEnabled] = useState(schedule?.enabled ?? false);
  const [intervalMinutes, setIntervalMinutes] = useState(
    String(schedule?.interval_minutes ?? 60),
  );
  const [retryDelayMinutes, setRetryDelayMinutes] = useState(
    String(schedule?.retry_delay_minutes ?? 15),
  );
  const [fetchLimit, setFetchLimit] = useState(String(schedule?.fetch_limit ?? 10));
  const [optionsText, setOptionsText] = useState(
    JSON.stringify(schedule?.options ?? {}, null, 2),
  );
  const saveKey = `schedule-save-${source.id}`;
  const runKey = `schedule-run-${source.id}`;

  useEffect(() => {
    setEnabled(schedule?.enabled ?? false);
    setIntervalMinutes(String(schedule?.interval_minutes ?? 60));
    setRetryDelayMinutes(String(schedule?.retry_delay_minutes ?? 15));
    setFetchLimit(String(schedule?.fetch_limit ?? 10));
    setOptionsText(JSON.stringify(schedule?.options ?? {}, null, 2));
  }, [schedule]);

  const save = async () => {
    const parsed = JSON.parse(optionsText) as unknown;
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
      throw new Error("运行参数必须是 JSON 对象");
    }
    return api(`/collection-schedules/sources/${source.id}`, {
      method: "PUT",
      body: JSON.stringify({
        enabled,
        interval_minutes: Number(intervalMinutes),
        retry_delay_minutes: Number(retryDelayMinutes),
        fetch_limit: Number(fetchLimit),
        options: parsed,
      }),
    });
  };

  return (
    <article className="admin-item collection-schedule-card">
      <div className="admin-item-meta">
        <span>SOURCE #{source.id}</span>
        <span>{connectorLabels[source.connector_type] ?? source.connector_type}</span>
        <b>{schedule?.last_status ?? "未配置"}</b>
      </div>
      <h3>{source.name}</h3>
      <div className="review-form collection-schedule-form">
        <label className="schedule-enabled">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          启用周期采集
        </label>
        <label>
          采集周期（分钟）
          <input
            type="number"
            min="5"
            max="10080"
            value={intervalMinutes}
            onChange={(event) => setIntervalMinutes(event.target.value)}
          />
        </label>
        <label>
          失败重试（分钟）
          <input
            type="number"
            min="1"
            max="1440"
            value={retryDelayMinutes}
            onChange={(event) => setRetryDelayMinutes(event.target.value)}
          />
        </label>
        <label>
          单次上限
          <input
            type="number"
            min="1"
            max="50"
            value={fetchLimit}
            onChange={(event) => setFetchLimit(event.target.value)}
          />
        </label>
        <label className="schedule-options">
          Connector 运行参数（JSON）
          <textarea
            value={optionsText}
            onChange={(event) => setOptionsText(event.target.value)}
          />
        </label>
      </div>
      <div className="collection-schedule-state">
        <span>
          下次执行：
          {schedule?.run_requested_at
            ? " 已进入立即运行队列"
            : schedule?.next_run_at
              ? new Date(schedule.next_run_at).toLocaleString("zh-CN")
              : " 未安排"}
        </span>
        <span>
          上次成功：
          {schedule?.last_success_at
            ? new Date(schedule.last_success_at).toLocaleString("zh-CN")
            : " 暂无"}
        </span>
        {schedule?.last_connector_run_id && (
          <span>Connector Run #{schedule.last_connector_run_id}</span>
        )}
      </div>
      {schedule?.last_error && <pre>{schedule.last_error}</pre>}
      {!source.is_active && <p className="review-note">该来源已停用，无法调度或立即运行。</p>}
      <div className="admin-actions">
        <button
          type="button"
          disabled={!source.is_active || busy === saveKey}
          onClick={() => void act(saveKey, save, "采集计划已保存")}
        >
          <Check size={14} /> 保存计划
        </button>
        <button
          className="approve"
          type="button"
          disabled={!source.is_active || busy === runKey}
          onClick={() =>
            void act(
              runKey,
              () =>
                api(`/collection-schedules/sources/${source.id}/run-now`, {
                  method: "POST",
                }),
              "已加入立即采集队列",
            )
          }
        >
          <Play size={14} /> 立即运行
        </button>
      </div>
    </article>
  );
}

function PipelinePanel({
  sources,
  schedules,
  connectorRuns,
  items,
  jobs,
  corrections,
  busy,
  act,
}: {
  sources: Source[];
  schedules: CollectionSchedule[];
  connectorRuns: ConnectorRun[];
  items: NormalizedItem[];
  jobs: PipelineJob[];
  corrections: PipelineCorrection[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [itemId, setItemId] = useState("");
  const [failedJobId, setFailedJobId] = useState("");
  const [stage, setStage] = useState("event_decision");
  const [mode, setMode] = useState("manual");
  const [reason, setReason] = useState("");
  const [automationView, setAutomationView] = useState<
    "collection" | "pipeline" | "recovery"
  >("collection");
  const [selectedSourceId, setSelectedSourceId] = useState<number | null>(null);
  const [showCollectionLogs, setShowCollectionLogs] = useState(false);
  const [collectionLogPage, setCollectionLogPage] = useState(1);
  const [pipelineLogPage, setPipelineLogPage] = useState(1);
  const [correctionLogPage, setCorrectionLogPage] = useState(1);
  const collectionSources = sources.filter(
    (source) => source.connector_type !== "manual",
  );
  const selectedSource =
    collectionSources.find((source) => source.id === selectedSourceId) ??
    collectionSources[0];
  const selectedSourceRuns = connectorRuns.filter(
    (run) => run.source_id === selectedSource?.id,
  );
  const collectionLogPageCount = Math.max(
    1,
    Math.ceil(selectedSourceRuns.length / AUTOMATION_LOG_PAGE_SIZE),
  );
  const visibleCollectionRuns = pageItems(
    selectedSourceRuns,
    collectionLogPage,
    AUTOMATION_LOG_PAGE_SIZE,
  );
  const pipelineLogPageCount = Math.max(
    1,
    Math.ceil(jobs.length / AUTOMATION_LOG_PAGE_SIZE),
  );
  const visiblePipelineJobs = pageItems(
    jobs,
    pipelineLogPage,
    AUTOMATION_LOG_PAGE_SIZE,
  );
  const correctionLogPageCount = Math.max(
    1,
    Math.ceil(corrections.length / AUTOMATION_LOG_PAGE_SIZE),
  );
  const visibleCorrections = pageItems(
    corrections,
    correctionLogPage,
    AUTOMATION_LOG_PAGE_SIZE,
  );
  const failedJobs = latestPipelineJobsByRawItem(jobs)
    .filter((job) => job.status === "failed");
  const payload = {
    restart_from_stage: stage,
    resume_mode: mode,
    reason: reason.trim(),
  };

  useEffect(() => {
    if (selectedSource && selectedSourceId === null) {
      setSelectedSourceId(selectedSource.id);
    }
  }, [selectedSource, selectedSourceId]);

  useEffect(() => {
    setCollectionLogPage(1);
  }, [selectedSourceId]);

  useEffect(() => {
    if (collectionLogPage > collectionLogPageCount) {
      setCollectionLogPage(collectionLogPageCount);
    }
  }, [collectionLogPage, collectionLogPageCount]);

  useEffect(() => {
    if (pipelineLogPage > pipelineLogPageCount) {
      setPipelineLogPage(pipelineLogPageCount);
    }
  }, [pipelineLogPage, pipelineLogPageCount]);

  useEffect(() => {
    if (correctionLogPage > correctionLogPageCount) {
      setCorrectionLogPage(correctionLogPageCount);
    }
  }, [correctionLogPage, correctionLogPageCount]);

  return (
    <section className="admin-panel automation-workbench">
      <div className="automation-view-tabs" role="tablist" aria-label="自动化功能">
        <button
          className={automationView === "collection" ? "active" : ""}
          type="button"
          role="tab"
          aria-selected={automationView === "collection"}
          onClick={() => setAutomationView("collection")}
        >
          自动化采集
        </button>
        <button
          className={automationView === "pipeline" ? "active" : ""}
          type="button"
          role="tab"
          aria-selected={automationView === "pipeline"}
          onClick={() => setAutomationView("pipeline")}
        >
          自动化管线日志 <b>{jobs.length}</b>
        </button>
        <button
          className={automationView === "recovery" ? "active" : ""}
          type="button"
          role="tab"
          aria-selected={automationView === "recovery"}
          onClick={() => setAutomationView("recovery")}
        >
          撤回与恢复 <b>{corrections.length}</b>
        </button>
      </div>
      <article
        className="review-card automation-column automation-collection-column"
        hidden={automationView !== "collection"}
      >
        <div className="review-heading">
          <div>
            <span>COLLECTION SCHEDULER</span>
            <h3>按来源配置自动采集周期</h3>
          </div>
        </div>
        <p>
          启用后由独立调度进程周期采集；首次采集不设时间水位，之后从上次成功时间继续。
          “立即运行”不改变启用状态，失败后按重试间隔再次执行。
        </p>
        <div className="collection-source-grid">
          {collectionSources.map((source) => {
            const sourceSchedule = schedules.find(
              (row) => row.source_id === source.id,
            );
            return (
              <button
                className={selectedSource?.id === source.id ? "active" : ""}
                key={source.id}
                type="button"
                onClick={() => {
                  setSelectedSourceId(source.id);
                  setShowCollectionLogs(false);
                }}
              >
                <span>{connectorLabels[source.connector_type] ?? source.connector_type}</span>
                <strong>{source.name}</strong>
                <small>
                  {sourceSchedule?.enabled
                    ? sourceSchedule.last_status === "running"
                      ? "采集中"
                      : "已启用"
                    : "未启用"}
                </small>
              </button>
            );
          })}
        </div>
        {selectedSource && (
          <>
            <SourceScheduleCard
              source={selectedSource}
              schedule={schedules.find((row) => row.source_id === selectedSource.id)}
              busy={busy}
              act={act}
            />
            <button
              className="collection-log-toggle"
              type="button"
              onClick={() => setShowCollectionLogs((value) => !value)}
            >
              <FileClock size={14} />
              {showCollectionLogs ? "收起采集日志" : "打开采集日志"}
              <b>{selectedSourceRuns.length}</b>
            </button>
            {showCollectionLogs && (
              <div className="collection-run-list">
                {visibleCollectionRuns.map((run) => (
                  <article className="automation-log-entry" key={run.id}>
                    <div className="admin-item-meta">
                      <span>RUN #{run.id}</span>
                      <span>{new Date(run.started_at).toLocaleString("zh-CN")}</span>
                      <b>{run.status}</b>
                    </div>
                    <p>
                      发现 {run.discovered_count} · 新增 {run.created_count} · 修订{" "}
                      {run.revised_count} · 跳过 {run.skipped_count}
                    </p>
                    {run.error_message && <pre>{run.error_message}</pre>}
                  </article>
                ))}
                {!selectedSourceRuns.length && (
                  <p className="automation-empty">该信源还没有采集记录。</p>
                )}
                <Pagination
                  page={collectionLogPage}
                  pageCount={collectionLogPageCount}
                  total={selectedSourceRuns.length}
                  onChange={setCollectionLogPage}
                />
              </div>
            )}
          </>
        )}
      </article>

      <article
        className="review-card automation-column automation-recovery-column"
        hidden={automationView !== "recovery"}
      >
        <div className="review-heading">
          <div>
            <span>PIPELINE RECOVERY</span>
            <h3>撤回已发布结果，或恢复失败的自动任务</h3>
          </div>
        </div>
        <p>
          选择“事件判断”只撤回事件成员；选择更早阶段会立即隐藏消息，重新发布后再进入事件聚合。
        </p>
        <div className="review-form">
          <label>
            已发布消息
            <select value={itemId} onChange={(event) => setItemId(event.target.value)}>
              <option value="">选择消息</option>
              {items.map((item) => (
                <option key={item.id} value={item.id}>
                  #{item.id} · {item.normalized_title}
                </option>
              ))}
            </select>
          </label>
          <label>
            失败任务
            <select
              value={failedJobId}
              onChange={(event) => setFailedJobId(event.target.value)}
            >
              <option value="">选择失败任务</option>
              {failedJobs.map((job) => (
                <option key={job.id} value={job.id}>
                  Job #{job.id} · Raw #{job.raw_item_id} · {job.current_stage}
                </option>
              ))}
            </select>
          </label>
          <label>
            从哪一步重新开始
            <select value={stage} onChange={(event) => setStage(event.target.value)}>
              <option value="relevance">相关性</option>
              <option value="image_ocr">图片 OCR</option>
              <option value="translation">翻译</option>
              <option value="item_analysis">摘要与分析</option>
              <option value="event_decision">事件判断</option>
            </select>
          </label>
          <label>
            后续模式
            <select value={mode} onChange={(event) => setMode(event.target.value)}>
              <option value="manual">人工审核</option>
              <option value="automatic">自动跑完</option>
            </select>
          </label>
          <textarea
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="填写撤回或恢复原因"
          />
        </div>
        <div className="admin-actions">
          <button
            className="reject"
            type="button"
            disabled={!itemId || !reason.trim() || busy === "pipeline-correct"}
            onClick={() =>
              void act(
                "pipeline-correct",
                () =>
                  api(`/pipeline/normalized-items/${itemId}/corrections`, {
                    method: "POST",
                    body: JSON.stringify(payload),
                  }),
                "已撤回并按所选模式启动纠正流程",
              )
            }
          >
            <RotateCcw size={14} /> 撤回并重跑
          </button>
          <button
            type="button"
            disabled={!failedJobId || !reason.trim() || busy === "pipeline-recover"}
            onClick={() =>
              void act(
                "pipeline-recover",
                () =>
                  api(`/pipeline/jobs/${failedJobId}/recover`, {
                    method: "POST",
                    body: JSON.stringify(payload),
                  }),
                "失败任务已从所选阶段恢复",
              )
            }
          >
            <Play size={14} /> 恢复失败任务
          </button>
        </div>
        <div className="automation-sublog">
          <h4>最近撤回记录</h4>
          {visibleCorrections.map((correction) => (
            <article className="automation-log-entry" key={`correction-${correction.id}`}>
              <div className="admin-item-meta">
                <span>CORRECTION #{correction.id}</span>
                <span>{correction.restart_from_stage}</span>
                <span>{correction.resume_mode}</span>
                <b>{correction.status}</b>
              </div>
              <p>{correction.reason}</p>
            </article>
          ))}
          {!corrections.length && <p className="automation-empty">暂无撤回记录。</p>}
          <Pagination
            page={correctionLogPage}
            pageCount={correctionLogPageCount}
            total={corrections.length}
            onChange={setCorrectionLogPage}
          />
        </div>
      </article>

      <div
        className="admin-list automation-column automation-pipeline-column"
        hidden={automationView !== "pipeline"}
      >
        <div className="review-heading">
          <div>
            <span>AUTOMATIC PIPELINE</span>
            <h3>自动化管线日志</h3>
          </div>
        </div>
        {visiblePipelineJobs.map((job) => (
          <article className="admin-item" key={job.id}>
            <div className="admin-item-meta">
              <span>JOB #{job.id}</span>
              <span>RAW #{job.raw_item_id}</span>
              <span>{job.current_stage}</span>
              <b>{job.status}</b>
            </div>
            <p>
              尝试 {job.attempts} 次
              {job.last_checkpoint_id
                ? ` · 最后检查点 #${job.last_checkpoint_id}`
                : " · 尚无有效检查点"}
            </p>
            {job.error_message && <pre>{job.error_message}</pre>}
          </article>
        ))}
        {!jobs.length && <p className="automation-empty">暂无自动化管线日志。</p>}
        <Pagination
          page={pipelineLogPage}
          pageCount={pipelineLogPageCount}
          total={jobs.length}
          onChange={setPipelineLogPage}
        />
      </div>
    </section>
  );
}

function EventAggregationPanel({
  items,
  runs,
  rawItems,
  busy,
  act,
}: {
  items: NormalizedItem[];
  runs: EventAggregationRun[];
  rawItems: RawItem[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [eventView, setEventView] = useState<"unreviewed" | "reviewed">("unreviewed");
  const [sortDirection, setSortDirection] = useState<TimeSort>("desc");
  const [page, setPage] = useState(1);
  const latestRuns = useMemo(() => {
    const values = new Map<number, EventAggregationRun>();
    for (const run of runs) {
      const current = values.get(run.normalized_item_id);
      if (!current || run.id > current.id) values.set(run.normalized_item_id, run);
    }
    return values;
  }, [runs]);
  const rawById = useMemo(
    () => new Map(rawItems.map((item) => [item.id, item])),
    [rawItems],
  );
  const reviewedItems = items.filter(
    (item) => latestRuns.get(item.id)?.status === "completed",
  );
  const unreviewedItems = items.filter(
    (item) => latestRuns.get(item.id)?.status !== "completed",
  );
  const selectedItems = eventView === "reviewed" ? reviewedItems : unreviewedItems;
  const sortedItems = sortedByMessageTime(
    selectedItems,
    (item) => rawById.get(item.raw_item_id),
    sortDirection,
  );
  const pageCount = Math.max(1, Math.ceil(sortedItems.length / EVENT_PAGE_SIZE));
  const visibleItems = pageItems(sortedItems, page, EVENT_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [eventView, sortDirection]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <section className="admin-panel">
      <div className="list-controls">
        <div className="item-status-switch" role="tablist" aria-label="事件聚合状态">
          <button
            type="button"
            role="tab"
            aria-selected={eventView === "unreviewed"}
            className={eventView === "unreviewed" ? "active" : ""}
            onClick={() => setEventView("unreviewed")}
          >
            未审核 <span>{unreviewedItems.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={eventView === "reviewed"}
            className={eventView === "reviewed" ? "active" : ""}
            onClick={() => setEventView("reviewed")}
          >
            已审核 <span>{reviewedItems.length}</span>
          </button>
        </div>
        <TimeSortControl value={sortDirection} onChange={setSortDirection} />
      </div>
      <div className="admin-list">
        {!visibleItems.length && (
          <div className="admin-empty">
            {eventView === "reviewed"
              ? "目前没有已审核的事件聚合消息。"
              : "目前没有待处理的事件聚合消息。"}
          </div>
        )}
        {visibleItems.map((item) => {
          const run = latestRuns.get(item.id);
          const canStart = !run;
          const canRetry = run && ["failed", "rejected"].includes(run.status);
          return (
            <article className="admin-item" key={item.id}>
              <div className="admin-item-meta">
                <span>MESSAGE #{item.id}</span>
                <span>
                  {(() => {
                    const rawItem = rawById.get(item.raw_item_id);
                    return rawItem
                      ? new Date(rawItem.published_at ?? rawItem.ingested_at)
                          .toLocaleString("zh-CN")
                      : "无消息时间";
                  })()}
                </span>
                <span>{item.category}</span>
                <b>{run?.status ?? "未聚合"}</b>
              </div>
              <h3>{item.normalized_title}</h3>
              <p>{item.summary}</p>
              <div className="admin-actions">
                {(canStart || canRetry) && (
                  <button
                    type="button"
                    disabled={busy === `event-item-${item.id}`}
                    onClick={() =>
                      void act(
                        `event-item-${item.id}`,
                        () =>
                          api(
                            canRetry
                              ? `/event-workflows/runs/${run.id}/retry`
                              : `/event-workflows/items/${item.id}/process`,
                            { method: "POST" },
                          ),
                        canRetry ? "事件聚合已重新生成草稿" : "事件聚合草稿已生成",
                      )
                    }
                  >
                    {busy === `event-item-${item.id}` ? (
                      <LoaderCircle className="spin" size={14} />
                    ) : canRetry ? (
                      <RotateCcw size={14} />
                    ) : (
                      <Sparkles size={14} />
                    )}
                    {canRetry ? "重试事件判断" : "开始事件聚合"}
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      <Pagination
        page={page}
        pageCount={pageCount}
        total={sortedItems.length}
        onChange={setPage}
      />
    </section>
  );
}

function EventReviewCard({
  review,
  busy,
  act,
}: {
  review: EventReviewTask;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [reason, setReason] = useState("");
  const decision = review.proposal.decision as JsonObject | undefined;
  return (
    <article className="review-card">
      <div className="review-heading">
        <div>
          <span>EVENT REVIEW #{review.id} · RUN #{review.event_aggregation_run_id}</span>
          <h3>事件聚合决策：{String(decision?.decision ?? "未知")}</h3>
        </div>
      </div>
      <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
      <div className="review-form">
        <textarea
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          placeholder="拒绝时填写原因；可沉淀为事件聚合知识"
        />
      </div>
      <div className="admin-actions">
        <button
          className="approve"
          type="button"
          disabled={busy === `event-approve-${review.id}`}
          onClick={() =>
            void act(
              `event-approve-${review.id}`,
              () =>
                api(`/event-workflows/reviews/${review.id}/approve`, {
                  method: "POST",
                  body: JSON.stringify({ note: null }),
                }),
              "事件决策已批准并完成审计",
            )
          }
        >
          <Check size={14} /> 批准决策
        </button>
        <button
          className="reject"
          type="button"
          disabled={!reason.trim() || busy === `event-reject-${review.id}`}
          onClick={() =>
            void act(
              `event-reject-${review.id}`,
              () =>
                api(`/event-workflows/reviews/${review.id}/reject`, {
                  method: "POST",
                  body: JSON.stringify({
                    reason: reason.trim(),
                    knowledge_rule: reason.trim(),
                    knowledge_scope: "global",
                  }),
                }),
              "事件决策已拒绝并保留纠错记录",
            )
          }
        >
          <X size={14} /> 拒绝并沉淀知识
        </button>
      </div>
    </article>
  );
}

function ItemsPanel({
  rawItems,
  runs,
  busy,
  act,
}: {
  rawItems: RawItem[];
  runs: ProcessingRun[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [itemView, setItemView] = useState<"incomplete" | "completed">("incomplete");
  const [sortDirection, setSortDirection] = useState<TimeSort>("desc");
  const [page, setPage] = useState(1);
  const retryRuns = latestRunsByRawItem(runs)
    .filter((run) => ["failed", "rejected"].includes(run.status));
  const completedItems = rawItems.filter(
    (item) => item.processing_status === "analyzed" || item.processing_status === "completed",
  );
  const incompleteItems = rawItems.filter(
    (item) => item.processing_status !== "analyzed" && item.processing_status !== "completed",
  );
  const selectedItems = itemView === "completed" ? completedItems : incompleteItems;
  const sortedItems = sortedByMessageTime(
    selectedItems,
    (item) => item,
    sortDirection,
  );
  const pageCount = Math.max(1, Math.ceil(sortedItems.length / ITEM_PAGE_SIZE));
  const visibleItems = pageItems(sortedItems, page, ITEM_PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [itemView, sortDirection]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <section className="admin-panel">
      <div className="list-controls">
        <div className="item-status-switch" role="tablist" aria-label="单条处理状态">
          <button
            type="button"
            role="tab"
            aria-selected={itemView === "incomplete"}
            className={itemView === "incomplete" ? "active" : ""}
            onClick={() => setItemView("incomplete")}
          >
            未审核完成 <span>{incompleteItems.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={itemView === "completed"}
            className={itemView === "completed" ? "active" : ""}
            onClick={() => setItemView("completed")}
          >
            已审核 <span>{completedItems.length}</span>
          </button>
        </div>
        <TimeSortControl value={sortDirection} onChange={setSortDirection} />
      </div>
      {itemView === "incomplete" && retryRuns.length > 0 && (
        <div className="retry-strip">
          <strong>需要修订或重试</strong>
          {retryRuns.map((run) => (
            <button
              key={run.id}
              type="button"
              disabled={busy === `retry-${run.id}`}
              onClick={() =>
                void act(
                  `retry-${run.id}`,
                  () => api(`/workflows/runs/${run.id}/retry`, { method: "POST" }),
                  `运行 #${run.id} 已重新生成审核草稿`,
                )
              }
            >
              <RotateCcw size={13} /> #{run.id} · {run.current_stage}
            </button>
          ))}
        </div>
      )}
      <div className="admin-list">
        {selectedItems.length === 0 && (
          <div className="admin-empty">
            {itemView === "completed" ? "目前没有已审核消息。" : "目前没有未完成的单条消息。"}
          </div>
        )}
        {visibleItems.map((item) => {
          const canStart = item.processing_status === "pending";
          return (
            <article className="admin-item" key={item.id}>
              <div className="admin-item-meta">
                <span>RAW #{item.id}</span>
                <span>{item.published_at ? new Date(item.published_at).toLocaleString("zh-CN") : "无发布日期"}</span>
                <b>{item.processing_status}</b>
              </div>
              <h3>{item.display_title ?? "无标题信息"}</h3>
              <p>{item.author_name ?? `Source #${item.source_id}`}</p>
              <div className="admin-actions">
                {item.canonical_url && <a href={item.canonical_url} target="_blank" rel="noreferrer">查看原文</a>}
                {canStart && (
                  <button
                    type="button"
                    disabled={busy === `raw-${item.id}`}
                    onClick={() =>
                      void act(
                        `raw-${item.id}`,
                        () => api(`/raw-items/${item.id}/process`, { method: "POST" }),
                        `Raw #${item.id} 已进入相关性审核`,
                      )
                    }
                  >
                    {busy === `raw-${item.id}` ? <LoaderCircle className="spin" size={14} /> : <Play size={14} />}
                    开始 AI 处理
                  </button>
                )}
              </div>
            </article>
          );
        })}
      </div>
      <Pagination
        page={page}
        pageCount={pageCount}
        total={sortedItems.length}
        onChange={setPage}
      />
    </section>
  );
}

const messageReviewStages = [
  ["relevance", "相关性审核"],
  ["image_ocr", "OCR 审核"],
  ["translation", "翻译审核"],
  ["item_analysis", "分析审核"],
] as const;

function ReviewCenterPanel({
  reviews,
  eventReviews,
  runs,
  eventRuns,
  normalizedItems,
  rawItems,
  sources,
  mediaExtractions,
  ocrAssets,
  busy,
  act,
}: {
  reviews: ReviewTask[];
  eventReviews: EventReviewTask[];
  runs: ProcessingRun[];
  eventRuns: EventAggregationRun[];
  normalizedItems: NormalizedItem[];
  rawItems: RawItem[];
  sources: Source[];
  mediaExtractions: MediaExtraction[];
  ocrAssets: OCRAsset[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [reviewKind, setReviewKind] = useState<"message" | "event">("message");
  const [messageStage, setMessageStage] =
    useState<(typeof messageReviewStages)[number][0]>("relevance");
  const [sortDirection, setSortDirection] = useState<TimeSort>("desc");
  const [page, setPage] = useState(1);
  const rawById = useMemo(
    () => new Map(rawItems.map((item) => [item.id, item])),
    [rawItems],
  );
  const sourceById = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources],
  );
  const runById = useMemo(
    () => new Map(runs.map((run) => [run.id, run])),
    [runs],
  );
  const eventRunById = useMemo(
    () => new Map(eventRuns.map((run) => [run.id, run])),
    [eventRuns],
  );
  const normalizedById = useMemo(
    () => new Map(normalizedItems.map((item) => [item.id, item])),
    [normalizedItems],
  );
  const messageReviews = reviews.filter((review) => review.stage === messageStage);
  const sortedMessageReviews = sortedByMessageTime(
    messageReviews,
    (review) => {
      const run = runById.get(review.processing_run_id);
      return rawById.get(run?.raw_item_id ?? -1);
    },
    sortDirection,
  );
  const sortedEventReviews = sortedByMessageTime(
    eventReviews,
    (review) => {
      const run = eventRunById.get(review.event_aggregation_run_id);
      const item = normalizedById.get(run?.normalized_item_id ?? -1);
      return rawById.get(item?.raw_item_id ?? -1);
    },
    sortDirection,
  );
  const selectedReviews =
    reviewKind === "message" ? sortedMessageReviews : sortedEventReviews;
  const pageCount = Math.max(1, Math.ceil(selectedReviews.length / REVIEW_PAGE_SIZE));
  const visibleMessageReviews =
    reviewKind === "message"
      ? pageItems(sortedMessageReviews, page, REVIEW_PAGE_SIZE)
      : [];
  const visibleEventReviews =
    reviewKind === "event"
      ? pageItems(sortedEventReviews, page, REVIEW_PAGE_SIZE)
      : [];

  useEffect(() => {
    setPage(1);
  }, [reviewKind, messageStage, sortDirection]);

  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  return (
    <section className="admin-panel">
      <div className="list-controls">
        <div className="item-status-switch" role="tablist" aria-label="审核类型">
          <button
            type="button"
            role="tab"
            aria-selected={reviewKind === "message"}
            className={reviewKind === "message" ? "active" : ""}
            onClick={() => setReviewKind("message")}
          >
            消息审核 <span>{reviews.length}</span>
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={reviewKind === "event"}
            className={reviewKind === "event" ? "active" : ""}
            onClick={() => setReviewKind("event")}
          >
            事件聚合审核 <span>{eventReviews.length}</span>
          </button>
        </div>
        <TimeSortControl value={sortDirection} onChange={setSortDirection} />
      </div>
      {reviewKind === "message" && (
        <div className="review-stage-switch" role="tablist" aria-label="消息审核阶段">
          {messageReviewStages.map(([stage, label]) => (
            <button
              type="button"
              role="tab"
              aria-selected={messageStage === stage}
              className={messageStage === stage ? "active" : ""}
              key={stage}
              onClick={() => setMessageStage(stage)}
            >
              {label}
              <span>{reviews.filter((review) => review.stage === stage).length}</span>
            </button>
          ))}
        </div>
      )}
      <div className="admin-list">
        {!selectedReviews.length && (
          <div className="admin-empty">
            {reviewKind === "message"
              ? `目前没有待处理的${stageLabels[messageStage] ?? messageStage}。`
              : "目前没有待处理的事件聚合审核。"}
          </div>
        )}
        {visibleMessageReviews.map((review) => {
          const run = runById.get(review.processing_run_id);
          const rawItem = rawById.get(run?.raw_item_id ?? -1);
          return (
            <ReviewCard
              review={review}
              source={sourceById.get(rawItem?.source_id ?? -1) ?? null}
              mediaExtractions={mediaExtractions}
              ocrAssets={ocrAssets}
              busy={busy}
              act={act}
              key={review.id}
            />
          );
        })}
        {visibleEventReviews.map((review) => (
          <EventReviewCard
            key={review.id}
            review={review}
            busy={busy}
            act={act}
          />
        ))}
      </div>
      <Pagination
        page={page}
        pageCount={pageCount}
        total={selectedReviews.length}
        onChange={setPage}
      />
    </section>
  );
}

function ReviewCard({
  review,
  source,
  mediaExtractions,
  ocrAssets,
  busy,
  act,
}: {
  review: ReviewTask;
  source: Source | null;
  mediaExtractions: MediaExtraction[];
  ocrAssets: OCRAsset[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const approvedExtractionIds = Array.isArray(review.proposal.approved_media_extraction_ids)
    ? review.proposal.approved_media_extraction_ids.filter(
        (value): value is number => typeof value === "number",
      )
    : [];
  const reviewExtractions = mediaExtractions.filter((extraction) =>
    approvedExtractionIds.includes(extraction.id),
  );
  const defaultFeedbackType =
    review.stage === "relevance"
      ? "relevance_correction"
      : review.stage === "image_ocr"
        ? "ocr_error"
        : review.stage === "translation"
          ? "translation_correction"
          : "analysis_correction";
  const [reason, setReason] = useState("");
  const [glossaryUpdates, setGlossaryUpdates] = useState<GlossaryCorrectionDraft[]>([
    { id: 1, source_term: "", preferred_translation: "" },
  ]);
  const [feedbackType, setFeedbackType] = useState(defaultFeedbackType);
  const [knowledgeScope, setKnowledgeScope] = useState("global");
  const [ocrDrafts, setOcrDrafts] = useState<Record<number, OCRCorrectionDraft>>({});
  const updateOCRDraft = useCallback((draft: OCRCorrectionDraft) => {
    setOcrDrafts((current) => ({ ...current, [draft.extractionId]: draft }));
  }, []);
  const learnsRule = [
    "relevance_correction",
    "analysis_correction",
  ].includes(feedbackType);
  const learnsTerm = ["translation_term", "translation_correction"].includes(
    feedbackType,
  );
  const trimmedReason = reason.trim();
  const completeGlossaryUpdates = glossaryUpdates.filter(
    (item) => item.source_term.trim() && item.preferred_translation.trim(),
  );
  const hasIncompleteGlossaryUpdate = glossaryUpdates.some(
    (item) =>
      Boolean(item.source_term.trim()) !== Boolean(item.preferred_translation.trim()),
  );
  const rejectPayload = {
    feedback_type: feedbackType,
    reason: trimmedReason || null,
    knowledge_rule: learnsRule ? reason : null,
    knowledge_scope: knowledgeScope,
    corrected_values: {},
    glossary_updates:
      learnsTerm
        ? completeGlossaryUpdates.map((item) => ({
            source_term: item.source_term.trim(),
            preferred_translation: item.preferred_translation.trim(),
            forbidden_translations: [],
          }))
        : [],
  };
  const rejectSuccess =
    feedbackType === "ocr_error"
      ? "草稿已退回；OCR 错误已记录，但不会写入知识或术语"
      : learnsTerm
        ? "草稿已退回，翻译规则和术语修正已分别沉淀"
        : "草稿已退回，反馈已成为可编辑的长期规则";
  const changedOCRDrafts = Object.values(ocrDrafts).filter((draft) => draft.dirty);
  const invalidOCRDrafts = Object.values(ocrDrafts).filter((draft) => draft.invalid);
  const changedOCRDraft = changedOCRDrafts.length === 1 ? changedOCRDrafts[0] : null;
  const ocrActionKey = changedOCRDraft
    ? `correct-ocr-${review.id}-${changedOCRDraft.extractionId}`
    : `correct-ocr-${review.id}`;
  return (
    <article className="review-card">
      <div className="review-heading">
        <div>
          <span>REVIEW #{review.id} · RUN #{review.processing_run_id}</span>
          <h3>{stageLabels[review.stage] ?? review.stage}</h3>
        </div>
        <b>等待确认</b>
      </div>
      {review.stage === "item_analysis" ? (
        <AnalysisReview proposal={review.proposal} />
      ) : review.stage === "translation" ? (
        <TranslationReview proposal={review.proposal} />
      ) : review.stage === "image_ocr" ? (
        <>
          {reviewExtractions.map((extraction) => (
            <OCRCorrectionEditor
              key={extraction.id}
              extraction={extraction}
              asset={ocrAssets.find(
                (asset) => asset.media_asset_id === extraction.media_asset_id,
              )}
              onDraftChange={updateOCRDraft}
            />
          ))}
        </>
      ) : (
        <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
      )}
      {review.stage !== "image_ocr" && (
        <div className="review-form">
          {["item_analysis", "translation"].includes(review.stage) ? (
          <div className="review-feedback-context">
            <strong>
              {review.stage === "translation"
                  ? "翻译术语修正"
                  : "分析与摘要修正"}
            </strong>
            <span>
              {review.stage === "translation"
                  ? "可只填写术语修正；如需说明句子译法，再填写退回理由。两者至少填写一种。"
                  : "说明分析、分类或摘要的问题，退回后会沉淀为分析规则。"}
            </span>
          </div>
        ) : (
          <label>
            反馈类型
            <select value={feedbackType} onChange={(event) => setFeedbackType(event.target.value)}>
            {review.stage === "relevance" && (
              <option value="relevance_correction">相关性判断错误（沉淀规则）</option>
            )}
            </select>
          </label>
          )}
          <label>
            退回理由
            <textarea
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder={
                review.stage === "translation"
                  ? "可选：说明句子应该如何翻译。"
                  : "说明错误和正确处理方式。"
              }
            />
          </label>
          {review.stage === "item_analysis" && learnsRule && (
            <label>
              规则适用范围
              <select
                value={knowledgeScope}
                onChange={(event) => setKnowledgeScope(event.target.value)}
              >
                <option value="global">全局（global）</option>
                {source && (
                  <option value={`source:${source.id}`}>
                    当前账号：{source.name}（source:{source.id}）
                  </option>
                )}
                {source && (
                  <option value={`connector:${source.connector_type}`}>
                    当前采集器：{connectorLabels[source.connector_type] ?? source.connector_type}
                    （connector:{source.connector_type}）
                  </option>
                )}
              </select>
            </label>
          )}
          {learnsTerm && (
            <div className="glossary-corrections">
              {glossaryUpdates.map((item, index) => (
                <div className="glossary-correction" key={item.id}>
                  <label>
                    错误原词
                    <input
                      value={item.source_term}
                      onChange={(event) =>
                        setGlossaryUpdates((current) =>
                          current.map((entry) =>
                            entry.id === item.id
                              ? { ...entry, source_term: event.target.value }
                              : entry,
                          ),
                        )
                      }
                    />
                  </label>
                  <label>
                    标准译名
                    <input
                      value={item.preferred_translation}
                      onChange={(event) =>
                        setGlossaryUpdates((current) =>
                          current.map((entry) =>
                            entry.id === item.id
                              ? { ...entry, preferred_translation: event.target.value }
                              : entry,
                          ),
                        )
                      }
                    />
                  </label>
                  <button
                    className="remove-glossary-row"
                    type="button"
                    disabled={glossaryUpdates.length === 1}
                    aria-label={`删除第 ${index + 1} 项术语修正`}
                    onClick={() =>
                      setGlossaryUpdates((current) =>
                        current.filter((entry) => entry.id !== item.id),
                      )
                    }
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <button
                className="add-glossary-row"
                type="button"
                onClick={() =>
                  setGlossaryUpdates((current) => [
                    ...current,
                    {
                      id: Math.max(...current.map((item) => item.id)) + 1,
                      source_term: "",
                      preferred_translation: "",
                    },
                  ])
                }
              >
                <Plus size={14} /> 添加术语修正
              </button>
            </div>
          )}
        </div>
      )}
      {["item_analysis", "translation"].includes(review.stage) && (
        <details className="review-json-details">
          <summary>查看完整审核草稿 JSON</summary>
          <pre>{JSON.stringify(review.proposal, null, 2)}</pre>
        </details>
      )}
      <div className="admin-actions">
        <button
          className="approve"
          type="button"
          disabled={
            changedOCRDrafts.length > 0 ||
            invalidOCRDrafts.length > 0 ||
            busy === `approve-${review.id}`
          }
          onClick={() => {
            void act(
              `approve-${review.id}`,
              () => api(`/workflows/reviews/${review.id}/approve`, {
                method: "POST",
                body: JSON.stringify({ note: null }),
              }),
              "审核已批准，正式数据或下一审核阶段已生成",
            );
          }}
        >
          <Check size={14} />{" "}
          {review.stage === "item_analysis"
            ? "批准分析，完成处理"
            : review.stage === "translation"
              ? "批准翻译，进入分析审核"
            : review.stage === "image_ocr"
              ? "批准 OCR"
              : "批准"}
        </button>
        {review.stage === "image_ocr" ? (
          <button
            className="ocr-correction-action"
            type="button"
            disabled={
              !changedOCRDraft ||
              changedOCRDraft.invalid ||
              changedOCRDrafts.length !== 1 ||
              busy === ocrActionKey
            }
            onClick={() => {
              if (!changedOCRDraft) return;
              void act(
                ocrActionKey,
                () =>
                  api(`/workflows/reviews/${review.id}/correct-ocr`, {
                    method: "POST",
                    body: JSON.stringify({
                      extraction_id: changedOCRDraft.extractionId,
                      table_data: changedOCRDraft.tableData,
                    }),
                  }),
                "OCR 修改已保存，草稿已退回并重新处理",
              );
            }}
          >
            <RotateCcw size={14} /> 保存修改并退回重新处理
          </button>
        ) : (
          <button
            className="reject"
            type="button"
            disabled={
              (!trimmedReason &&
                (!learnsTerm || completeGlossaryUpdates.length === 0)) ||
              (learnsTerm && hasIncompleteGlossaryUpdate) ||
              busy === `reject-${review.id}`
            }
            onClick={() =>
              void act(
                `reject-${review.id}`,
                () => api(`/workflows/reviews/${review.id}/reject`, { method: "POST", body: JSON.stringify(rejectPayload) }),
                rejectSuccess,
              )
            }
          >
            <X size={14} /> 退回并学习
          </button>
        )}
      </div>
    </article>
  );
}

function AnalysisReview({ proposal }: { proposal: JsonObject }) {
  const entities = Array.isArray(proposal.entities) ? proposal.entities : [];
  return (
    <section className="review-content-panel">
      <div className="review-field review-field-wide">
        <span>标准化标题</span>
        <strong>{textValue(proposal.normalized_title)}</strong>
      </div>
      <div className="review-field review-field-wide">
        <span>摘要</span>
        <p>{textValue(proposal.summary)}</p>
      </div>
      <div className="review-field">
        <span>分类</span>
        <strong>{textValue(proposal.category)}</strong>
      </div>
      <div className="review-field">
        <span>重要性</span>
        <strong>{scoreValue(proposal.importance_score)}</strong>
      </div>
      <div className="review-field">
        <span>可信度</span>
        <strong>
          {textValue(proposal.credibility) === "official" ? "官方确认" : "信源可信度"} · {scoreValue(proposal.credibility_score)}
        </strong>
      </div>
      <div className="review-field review-field-wide">
        <span>重要性依据</span>
        <p>
          {Array.isArray(proposal.importance_evidence)
            ? proposal.importance_evidence.map(textValue).join("；")
            : "—"}
        </p>
      </div>
      <div className="review-field review-field-wide">
        <span>实体</span>
        <div className="review-entity-list">
          {entities.length ? (
            entities.map((entity, index) => (
              <code key={index}>{entityLabel(entity)}</code>
            ))
          ) : (
            <em>未提取实体</em>
          )}
        </div>
      </div>
    </section>
  );
}

function TranslationReview({ proposal }: { proposal: JsonObject }) {
  const sourceStructures = Array.isArray(proposal.media_extractions)
    ? proposal.media_extractions
    : [];
  const translatedStructures = Array.isArray(
    proposal.translated_media_extractions,
  )
    ? proposal.translated_media_extractions
    : [];
  return (
    <section className="translation-review">
      <div className="translation-meta">
        <span>{textValue(proposal.source_language)} → {textValue(proposal.target_language)}</span>
        <b>{textValue(proposal.translation_status)}</b>
        <small>{textValue(proposal.translation_model)}</small>
      </div>
      <div className="translation-title">
        <span>中文标题</span>
        <strong>{textValue(proposal.translated_title)}</strong>
      </div>
      <div className="translation-columns">
        <article>
          <span>原文</span>
          <p>{textValue(proposal.normalized_text)}</p>
        </article>
        <article>
          <span>中文译文</span>
          <p>{textValue(proposal.translated_text)}</p>
        </article>
      </div>
      {sourceStructures.map((sourceStructure, index) => {
        const translated = translatedStructures[index];
        const translatedData =
          translated && typeof translated === "object"
            ? (translated as JsonObject).translated_data
            : null;
        return (
          <div className="translation-columns" key={`patch-translation-${index}`}>
            <article>
              <span>版本图片结构化原文 {index + 1}</span>
              <pre>{JSON.stringify(sourceStructure, null, 2)}</pre>
            </article>
            <article>
              <span>版本图片结构化中文 {index + 1}</span>
              <pre>{JSON.stringify(translatedData, null, 2)}</pre>
            </article>
          </div>
        );
      })}
    </section>
  );
}

function OCRCorrectionEditor({
  extraction,
  asset,
  onDraftChange,
}: {
  extraction: MediaExtraction;
  asset?: OCRAsset;
  onDraftChange: (draft: OCRCorrectionDraft) => void;
}) {
  const sourceTable = extraction.processing_config.table_data;
  const [tableData, setTableData] = useState<PatchTableData>(() =>
    sourceTable ? JSON.parse(JSON.stringify(sourceTable)) as PatchTableData : {},
  );
  const [activeSectionIndex, setActiveSectionIndex] = useState(0);
  const [activeRecordIndex, setActiveRecordIndex] = useState(0);
  const [imageExpanded, setImageExpanded] = useState(false);
  const changesTextareaRef = useRef<HTMLTextAreaElement>(null);
  const sections = tableData.sections ?? [];
  const activeSection = sections[activeSectionIndex] ?? sections[0];
  const activeRecord = activeSection?.records[activeRecordIndex] ?? activeSection?.records[0];
  const sourceSectionLabel =
    sourceTable?.sections?.[activeSectionIndex]?.label ?? activeSection?.label;
  const activeSectionConfidence = findSectionOCRConfidence(
    extraction.ocr_lines,
    sourceSectionLabel,
  );

  useEffect(() => {
    const textarea = changesTextareaRef.current;
    if (!textarea) return;
    textarea.style.height = "0px";
    textarea.style.height = `${Math.max(textarea.scrollHeight, 180)}px`;
  }, [activeRecord?.raw_changes]);

  useEffect(() => {
    if (!imageExpanded) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setImageExpanded(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [imageExpanded]);

  const updateRecord = (
    sectionIndex: number,
    recordIndex: number,
    update: Partial<PatchTableRecord>,
  ) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? section.records.map((record, currentRecordIndex) =>
                currentRecordIndex === recordIndex ? { ...record, ...update } : record,
              )
            : section.records,
      })),
    }));
  };

  const removeRecord = (sectionIndex: number, recordIndex: number) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? section.records.filter((_, currentRecordIndex) => currentRecordIndex !== recordIndex)
            : section.records,
      })),
    }));
  };

  const addRecord = (sectionIndex: number) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) => ({
        ...section,
        records:
          currentSectionIndex === sectionIndex
            ? [
                ...section.records,
                { target: "", raw_changes: [""], bbox: [], ocr_confidence: 1 },
              ]
            : section.records,
      })),
    }));
  };

  const updateSectionLabel = (sectionIndex: number, label: string) => {
    setTableData((current) => ({
      ...current,
      sections: (current.sections ?? []).map((section, currentSectionIndex) =>
        currentSectionIndex === sectionIndex ? { ...section, label } : section,
      ),
    }));
  };

  useEffect(() => {
    const normalizedSections = normalizeOCRSections(tableData.sections ?? []);
    const sourceNormalizedSections = normalizeOCRSections(sourceTable?.sections ?? []);
    const dirty =
      JSON.stringify(normalizedSections) !== JSON.stringify(sourceNormalizedSections);
    const invalid = normalizedSections.some(
      (section) =>
        !section.label ||
        !section.records.length ||
        section.records.some((record) => !record.target),
    );
    onDraftChange({
      extractionId: extraction.id,
      tableData: {
        ...tableData,
        preview_kind: tableData.preview_kind ?? "preview",
        structure_confidence: 1,
        sections: normalizedSections,
      },
      dirty,
      invalid,
    });
  }, [extraction.id, onDraftChange, sourceTable, tableData]);

  if (!sourceTable || !sections.length) {
    return (
      <div className="ocr-review-editor">
        <strong>OCR 人工修订</strong>
        <p>这条图片提取没有可编辑的表格结构，当前只能直接批准审核。</p>
      </div>
    );
  }

  return (
    <section className="ocr-review-editor">
      <div className="ocr-review-heading">
        <div>
          <strong>OCR 人工修订 · 提取 #{extraction.id}</strong>
          <p>直接修改识别结果；有改动时，使用审核卡片底部的按钮保存并退回重新处理。</p>
        </div>
        <span>{extraction.schema_version}</span>
      </div>
      <div className="ocr-group-tabs" role="tablist" aria-label="OCR 分组">
        {sections.map((section, sectionIndex) => (
          <button
            className={sectionIndex === activeSectionIndex ? "active" : ""}
            key={`${section.section_type}-${sectionIndex}`}
            type="button"
            role="tab"
            aria-selected={sectionIndex === activeSectionIndex}
            onClick={() => {
              setActiveSectionIndex(sectionIndex);
              setActiveRecordIndex(0);
            }}
          >
            <span>{section.label || `分组 ${sectionIndex + 1}`}</span>
            <b>{section.records.length}</b>
          </button>
        ))}
      </div>
      <div className="ocr-review-workspace">
        <figure className="ocr-reference-image">
          <figcaption>
            <span>原图对照</span>
            <small>{asset ? `MEDIA #${asset.media_asset_id}` : "未找到媒体信息"}</small>
          </figcaption>
          {asset?.storage_path ? (
            <button
              className="ocr-reference-trigger"
              type="button"
              aria-label="放大查看 OCR 原图"
              onClick={() => setImageExpanded(true)}
            >
              <Image
                src={asset.storage_path}
                alt={asset.raw_title ?? "Patch Preview OCR 原图"}
                width={asset.width ?? 1200}
                height={asset.height ?? 1600}
                sizes="(max-width: 900px) 100vw, 42vw"
                unoptimized
              />
              <span>点击放大查看</span>
            </button>
          ) : (
            <div className="ocr-reference-missing">原图暂不可用</div>
          )}
        </figure>
        {activeSection && (
          <div className="ocr-review-section">
            <div className="ocr-section-heading">
              <label>
                <span className="ocr-field-label">
                  分组标题
                  <small className={ocrConfidenceClass(activeSectionConfidence)}>
                    标题 OCR {ocrConfidenceValue(activeSectionConfidence)}
                  </small>
                </span>
                <input
                  value={activeSection.label}
                  onChange={(event) =>
                    updateSectionLabel(activeSectionIndex, event.target.value)
                  }
                />
              </label>
              <span>{activeSection.section_type}</span>
            </div>
            <div className="ocr-record-tabs" role="tablist" aria-label="当前分组对象">
              {activeSection.records.map((record, recordIndex) => (
                <button
                  className={recordIndex === activeRecordIndex ? "active" : ""}
                  key={`${activeSectionIndex}-${recordIndex}`}
                  type="button"
                  role="tab"
                  aria-selected={recordIndex === activeRecordIndex}
                  onClick={() => setActiveRecordIndex(recordIndex)}
                >
                  <b>{recordIndex + 1}</b>
                  <span>{record.target || "未命名对象"}</span>
                  <small className={ocrConfidenceClass(record.ocr_confidence)}>
                    {ocrConfidenceValue(record.ocr_confidence)}
                  </small>
                </button>
              ))}
            </div>
            {activeRecord && (
              <div className="ocr-review-record">
                <div className="ocr-record-meta">
                  <span>对象 {activeRecordIndex + 1}</span>
                  <small className={ocrConfidenceClass(activeRecord.ocr_confidence)}>
                    对象与具体改动 OCR 综合置信度{" "}
                    {ocrConfidenceValue(activeRecord.ocr_confidence)}
                  </small>
                </div>
                <label>
                  对象（左栏）
                  <input
                    value={activeRecord.target}
                    onChange={(event) =>
                      updateRecord(activeSectionIndex, activeRecordIndex, {
                        target: event.target.value,
                      })
                    }
                  />
                </label>
                <label>
                  具体改动（右栏，每行一项）
                  <textarea
                    ref={changesTextareaRef}
                    value={activeRecord.raw_changes.join("\n")}
                    placeholder={
                      tableData.preview_kind === "preview"
                        ? "普通 Preview 可以没有具体改动"
                        : "每行填写一项改动"
                    }
                    onChange={(event) =>
                      updateRecord(activeSectionIndex, activeRecordIndex, {
                        raw_changes: event.target.value.split("\n"),
                      })
                    }
                  />
                </label>
                <button
                  className="text-button danger"
                  type="button"
                  disabled={activeSection.records.length === 1}
                  onClick={() => {
                    removeRecord(activeSectionIndex, activeRecordIndex);
                    setActiveRecordIndex((current) =>
                      Math.max(0, Math.min(current, activeSection.records.length - 2)),
                    );
                  }}
                >
                  删除
                </button>
              </div>
            )}
            <button
              className="text-button add-record"
              type="button"
              onClick={() => {
                addRecord(activeSectionIndex);
                setActiveRecordIndex(activeSection.records.length);
              }}
            >
              添加对象
            </button>
          </div>
        )}
      </div>
      {imageExpanded && asset?.storage_path && (
        <div
          className="ocr-image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label="OCR 原图放大查看"
          onClick={() => setImageExpanded(false)}
        >
          <button
            className="ocr-lightbox-close"
            type="button"
            aria-label="关闭原图"
            onClick={() => setImageExpanded(false)}
          >
            <X size={22} />
          </button>
          <div className="ocr-lightbox-image" onClick={(event) => event.stopPropagation()}>
            <Image
              src={asset.storage_path}
              alt={asset.raw_title ?? "Patch Preview OCR 放大原图"}
              width={asset.width ?? 1600}
              height={asset.height ?? 2200}
              sizes="96vw"
              unoptimized
            />
          </div>
        </div>
      )}
    </section>
  );
}

function textValue(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "—";
}

function scoreValue(value: unknown): string {
  return typeof value === "number" ? `${Math.round(value * 100)} / 100` : "—";
}

function ocrConfidenceValue(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}

function ocrConfidenceClass(value: number | null | undefined): string {
  if (typeof value !== "number") return "ocr-confidence unknown";
  if (value < 0.8) return "ocr-confidence low";
  if (value < 0.9) return "ocr-confidence medium";
  return "ocr-confidence high";
}

function findSectionOCRConfidence(
  lines: OCRLine[],
  sectionLabel: string | undefined,
): number | null {
  if (!sectionLabel) return null;
  const normalizedLabel = normalizeOCRLabel(sectionLabel);
  const match = lines.find(
    (line) => normalizeOCRLabel(line.text) === normalizedLabel,
  );
  return match?.confidence ?? null;
}

function normalizeOCRLabel(value: string): string {
  return value
    .normalize("NFKC")
    .toUpperCase()
    .replace(/[^A-Z0-9\u4E00-\u9FFF]/g, "");
}

function entityLabel(value: unknown): string {
  if (!value || typeof value !== "object") return textValue(value);
  const entity = value as Record<string, unknown>;
  const fallback = Object.entries(entity).find(
    ([key, fieldValue]) =>
      !["name", "type", "canonical_name"].includes(key)
      && typeof fieldValue === "string"
      && fieldValue.trim().length > 0,
  );
  const name = textValue(entity.name ?? fallback?.[1]);
  const type = textValue(entity.type ?? fallback?.[0]);
  return type === "—" ? name : `${name} · ${type}`;
}

function normalizeOCRSections(sections: PatchTableSection[]): PatchTableSection[] {
  return sections.map((section) => ({
    ...section,
    label: section.label.trim(),
    records: section.records.map((record) => ({
      ...record,
      target: record.target.trim(),
      raw_changes: record.raw_changes.map((change) => change.trim()).filter(Boolean),
    })),
  }));
}

const defaultOCRParameters: OCRParameters = {
  scale: 1,
  grayscale: false,
  contrast: 1,
  sharpness: 1,
  text_score: null,
  box_thresh: null,
  unclip_ratio: null,
  use_cls: true,
  divider_x_ratio: null,
  line_brightness: 105,
  line_coverage: 0.82,
};

const ocrPresets: Record<string, OCRParameters> = {
  原图默认: defaultOCRParameters,
  小字放大: { ...defaultOCRParameters, scale: 2, sharpness: 1.25 },
  灰度增强: {
    ...defaultOCRParameters,
    scale: 2,
    grayscale: true,
    contrast: 1.35,
    sharpness: 1.2,
  },
};

function OCRLabPanel({
  assets,
  runs,
  profiles,
  busy,
  act,
}: {
  assets: OCRAsset[];
  runs: OCRTestRun[];
  profiles: OCRProfile[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [selectedAssetId, setSelectedAssetId] = useState<number | null>(assets[0]?.media_asset_id ?? null);
  const [selectedRun, setSelectedRun] = useState<OCRTestRun | null>(runs[0] ?? null);
  const [profileName, setProfileName] = useState("原图默认");
  const [parameters, setParameters] = useState<OCRParameters>(defaultOCRParameters);
  const effectiveAssetId = selectedAssetId ?? assets[0]?.media_asset_id ?? null;
  const selectedAsset = assets.find((asset) => asset.media_asset_id === effectiveAssetId);
  const activeProfile = profiles.find((profile) => profile.is_active);
  const assetRuns = runs.filter((run) => run.media_asset_id === effectiveAssetId);

  useEffect(() => {
    if (selectedAssetId === null && assets[0]) {
      setSelectedAssetId(assets[0].media_asset_id);
    }
  }, [assets, selectedAssetId]);

  useEffect(() => {
    if (selectedRun === null && effectiveAssetId !== null) {
      const latestRun = runs.find((run) => run.media_asset_id === effectiveAssetId);
      if (latestRun) setSelectedRun(latestRun);
    }
  }, [effectiveAssetId, runs, selectedRun]);

  const applyPreset = (name: string) => {
    setProfileName(name);
    setParameters({ ...ocrPresets[name] });
  };
  const setNumber = (key: keyof OCRParameters, value: string, optional = false) => {
    setParameters((current) => ({
      ...current,
      [key]: optional && value === "" ? null : Number(value),
    }));
  };

  if (!assets.length) {
    return (
      <section className="admin-empty">
        没有找到 @RiotPhroxzon 已下载到本地的图片。先运行对应 Connector 后再测试。
      </section>
    );
  }

  return (
    <section className="ocr-lab">
      <div className="ocr-toolbar">
        <label>
          测试图片
          <select
            value={effectiveAssetId ?? ""}
            onChange={(event) => {
              setSelectedAssetId(Number(event.target.value));
              setSelectedRun(null);
            }}
          >
            {assets.map((asset) => (
              <option value={asset.media_asset_id} key={asset.media_asset_id}>
                #{asset.media_asset_id} · {asset.raw_title ?? `Raw #${asset.raw_item_id}`} · 图 {asset.block_index + 1}
              </option>
            ))}
          </select>
        </label>
        <div className="ocr-active-profile">
          <span>生产 OCR 参数</span>
          <strong>{activeProfile ? activeProfile.name : "尚未激活，使用引擎默认值"}</strong>
        </div>
      </div>

      <div className="ocr-workbench">
        <aside className="ocr-controls">
          <div className="ocr-presets">
            {Object.keys(ocrPresets).map((name) => (
              <button type="button" key={name} onClick={() => applyPreset(name)}>{name}</button>
            ))}
          </div>
          <label>参数组名称<input value={profileName} onChange={(event) => setProfileName(event.target.value)} /></label>
          <div className="ocr-control-grid">
            <label>缩放倍数<input type="number" min="1" max="4" step="0.25" value={parameters.scale} onChange={(event) => setNumber("scale", event.target.value)} /></label>
            <label>对比度<input type="number" min="0.5" max="3" step="0.05" value={parameters.contrast} onChange={(event) => setNumber("contrast", event.target.value)} /></label>
            <label>锐度<input type="number" min="0.5" max="3" step="0.05" value={parameters.sharpness} onChange={(event) => setNumber("sharpness", event.target.value)} /></label>
            <label>文本分数阈值<input type="number" min="0" max="1" step="0.05" value={parameters.text_score ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("text_score", event.target.value, true)} /></label>
            <label>检测框阈值<input type="number" min="0" max="1" step="0.05" value={parameters.box_thresh ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("box_thresh", event.target.value, true)} /></label>
            <label>检测框扩张<input type="number" min="0.5" max="3" step="0.1" value={parameters.unclip_ratio ?? ""} placeholder="引擎默认" onChange={(event) => setNumber("unclip_ratio", event.target.value, true)} /></label>
            <label>分隔线位置比例<input type="number" min="0.1" max="0.4" step="0.005" value={parameters.divider_x_ratio ?? ""} placeholder="自动检测" onChange={(event) => setNumber("divider_x_ratio", event.target.value, true)} /></label>
            <label>表格线亮度<input type="number" min="40" max="220" step="1" value={parameters.line_brightness} onChange={(event) => setNumber("line_brightness", event.target.value)} /></label>
            <label>横线覆盖率<input type="number" min="0.5" max="1" step="0.01" value={parameters.line_coverage} onChange={(event) => setNumber("line_coverage", event.target.value)} /></label>
          </div>
          <div className="ocr-checks">
            <label><input type="checkbox" checked={parameters.grayscale} onChange={(event) => setParameters((current) => ({ ...current, grayscale: event.target.checked }))} /> 灰度化</label>
            <label><input type="checkbox" checked={parameters.use_cls} onChange={(event) => setParameters((current) => ({ ...current, use_cls: event.target.checked }))} /> 文字方向分类</label>
          </div>
          <button
            className="ocr-run-button"
            type="button"
            disabled={!effectiveAssetId || busy === "ocr-run"}
            onClick={() =>
              void act(
                "ocr-run",
                () =>
                  api<OCRTestRun>("/ocr-lab/runs", {
                    method: "POST",
                    body: JSON.stringify({
                      media_asset_id: effectiveAssetId,
                      profile_name: profileName,
                      parameters,
                    }),
                  }).then((result) => {
                    setSelectedRun(result);
                    return result;
                  }),
                "OCR 测试完成；结果已保存，未调用 LLM",
              )
            }
          >
            {busy === "ocr-run" ? <LoaderCircle className="spin" size={14} /> : <ScanText size={14} />}
            运行本地 OCR
          </button>
          {assetRuns.length > 0 && (
            <div className="ocr-history">
              <span>这张图的历史结果</span>
              {assetRuns.map((run) => (
                <button type="button" key={run.id} onClick={() => setSelectedRun(run)}>
                  #{run.id} · {run.profile_name} ·
                  {run.structure_confidence === null
                    ? " 旧版"
                    : ` 结构 ${(run.structure_confidence * 100).toFixed(1)}%`}
                </button>
              ))}
            </div>
          )}
        </aside>

        <div className="ocr-results">
          <div className="ocr-images">
            <figure>
              <figcaption>原图 · {selectedAsset?.width ?? "?"} × {selectedAsset?.height ?? "?"}</figcaption>
              {selectedAsset && (
                <Image
                  src={selectedAsset.storage_path}
                  alt="OCR 测试原图"
                  width={selectedAsset.width ?? 1200}
                  height={selectedAsset.height ?? 800}
                  unoptimized
                />
              )}
            </figure>
            <figure>
              <figcaption>
                识别框
                {selectedRun && ` · ${selectedRun.processed_width} × ${selectedRun.processed_height}`}
              </figcaption>
              {selectedRun?.overlay_path
                ? (
                    <Image
                      src={selectedRun.overlay_path}
                      alt="OCR 识别框叠加结果"
                      width={selectedRun.processed_width}
                      height={selectedRun.processed_height}
                      unoptimized
                    />
                  )
                : <div className="ocr-placeholder">运行后在这里检查每个识别框</div>}
            </figure>
            <figure>
              <figcaption>
                表格单元格
                {selectedRun?.structure_confidence !== null
                  && selectedRun?.structure_confidence !== undefined
                  && ` · 结构 ${(selectedRun.structure_confidence * 100).toFixed(1)}%`}
              </figcaption>
              {selectedRun?.table_overlay_path
                ? (
                    <Image
                      src={selectedRun.table_overlay_path}
                      alt="表格结构与键值配对叠加结果"
                      width={selectedRun.processed_width}
                      height={selectedRun.processed_height}
                      unoptimized
                    />
                  )
                : <div className="ocr-placeholder">新版测试会在这里标出分隔线和合并单元格</div>}
            </figure>
          </div>

          {selectedRun && (
            <>
              <div className="ocr-result-meta">
                <span>结果 #{selectedRun.id}</span>
                <strong>平均置信度 {(selectedRun.confidence * 100).toFixed(2)}%</strong>
                {selectedRun.structure_confidence !== null && (
                  <strong>结构置信度 {(selectedRun.structure_confidence * 100).toFixed(2)}%</strong>
                )}
                {selectedRun.table_data.preview_kind && (
                  <span>
                    {selectedRun.table_data.preview_kind === "full_preview" ? "Full Preview" : "Preview"}
                    {selectedRun.table_data.divider_x
                      ? ` · 分隔线 x=${selectedRun.table_data.divider_x}`
                      : " · 无详情列"}
                  </span>
                )}
                <span>{selectedRun.engine}</span>
                <button
                  type="button"
                  disabled={
                    busy === `ocr-activate-${selectedRun.id}`
                    || selectedRun.structure_confidence === null
                    || selectedRun.structure_confidence < 0.65
                  }
                  onClick={() =>
                    void act(
                      `ocr-activate-${selectedRun.id}`,
                      () => api(`/ocr-lab/runs/${selectedRun.id}/activate`, { method: "POST" }),
                      `参数组“${selectedRun.profile_name}”已设为生产 OCR 参数`,
                    )
                  }
                >
                  设为生产参数
                </button>
              </div>
              {selectedRun.table_data.warnings && selectedRun.table_data.warnings.length > 0 && (
                <div className="ocr-structure-warnings">
                  {selectedRun.table_data.warnings.map((warning) => <p key={warning}>{warning}</p>)}
                </div>
              )}
              {selectedRun.table_data.sections && selectedRun.table_data.sections.length > 0 && (
                <div className="ocr-pairs">
                  <div className="panel-title">
                    <h2>表格键值配对</h2>
                    <span>
                      {selectedRun.table_data.sections.reduce(
                        (total, section) => total + section.records.length,
                        0,
                      )} 个目标
                    </span>
                  </div>
                  {selectedRun.table_data.sections.map((section) => (
                    <section className="ocr-pair-section" key={`${selectedRun.id}-${section.section_type}`}>
                      <h3>{section.label} <span>{section.section_type}</span></h3>
                      {section.records.map((record) => (
                        <article className="ocr-pair" key={`${section.section_type}-${record.target}`}>
                          <strong>{record.target}</strong>
                          <div>
                            {record.raw_changes.length > 0
                              ? record.raw_changes.map((change, index) => (
                                  <p key={`${record.target}-${index}`}>{change}</p>
                                ))
                              : (
                                  <p className="ocr-no-change">
                                    {selectedRun.table_data.preview_kind === "preview"
                                      ? "Preview 仅公布目标，尚无具体数值"
                                      : "未识别到右侧改动，需要人工检查"}
                                  </p>
                                )}
                          </div>
                          <small>{(record.ocr_confidence * 100).toFixed(1)}%</small>
                        </article>
                      ))}
                    </section>
                  ))}
                </div>
              )}
              <div className="ocr-lines">
                <div className="ocr-line ocr-line-head"><span>#</span><span>识别文本</span><span>置信度</span></div>
                {selectedRun.lines.map((line) => (
                  <div className="ocr-line" key={`${selectedRun.id}-${line.index}`}>
                    <span>{line.index}</span>
                    <span>{line.text}</span>
                    <span>{line.confidence === null ? "—" : `${(line.confidence * 100).toFixed(1)}%`}</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function KnowledgePanel({
  rules,
  terms,
  busy,
  act,
}: {
  rules: KnowledgeRule[];
  terms: GlossaryTerm[];
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [knowledgeView, setKnowledgeView] = useState<"rules" | "terms">("rules");
  const [showNewRule, setShowNewRule] = useState(false);
  const [newRuleType, setNewRuleType] = useState("analysis");
  const [newRuleScope, setNewRuleScope] = useState("global");
  const [newRuleText, setNewRuleText] = useState("");
  const [showNewTerm, setShowNewTerm] = useState(false);
  const [newSourceTerm, setNewSourceTerm] = useState("");
  const [newTranslation, setNewTranslation] = useState("");
  const [rulePage, setRulePage] = useState(1);
  const [termPage, setTermPage] = useState(1);

  const rulePageCount = Math.max(1, Math.ceil(rules.length / KNOWLEDGE_PAGE_SIZE));
  const termPageCount = Math.max(1, Math.ceil(terms.length / GLOSSARY_PAGE_SIZE));
  const visibleRulePage = Math.min(rulePage, rulePageCount);
  const visibleTermPage = Math.min(termPage, termPageCount);
  const visibleRules = rules.slice(
    (visibleRulePage - 1) * KNOWLEDGE_PAGE_SIZE,
    visibleRulePage * KNOWLEDGE_PAGE_SIZE,
  );
  const visibleTerms = terms.slice(
    (visibleTermPage - 1) * GLOSSARY_PAGE_SIZE,
    visibleTermPage * GLOSSARY_PAGE_SIZE,
  );

  const createRule = async () => {
    await act(
      "create-rule",
      () => api("/knowledge/rules", {
        method: "POST",
        body: JSON.stringify({
          knowledge_type: newRuleType,
          scope: newRuleScope.trim(),
          rule_text: newRuleText.trim(),
        }),
      }),
      "规则已添加",
    );
    setNewRuleText("");
    setShowNewRule(false);
    setRulePage(1);
  };

  const createTerm = async () => {
    await act(
      "create-term",
      () => api("/knowledge/glossary", {
        method: "POST",
        body: JSON.stringify({
          source_term: newSourceTerm.trim(),
          preferred_translation: newTranslation.trim(),
        }),
      }),
      "术语已添加",
    );
    setNewSourceTerm("");
    setNewTranslation("");
    setShowNewTerm(false);
    setTermPage(1);
  };

  return (
    <section className="knowledge-panel">
      <div className="knowledge-switch" role="tablist" aria-label="知识与术语">
        <button
          type="button"
          role="tab"
          aria-selected={knowledgeView === "rules"}
          className={knowledgeView === "rules" ? "active" : ""}
          onClick={() => setKnowledgeView("rules")}
        >
          判断规则
          <span>{rules.filter((rule) => rule.is_active).length}</span>
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={knowledgeView === "terms"}
          className={knowledgeView === "terms" ? "active" : ""}
          onClick={() => setKnowledgeView("terms")}
        >
          翻译术语
          <span>{terms.filter((term) => term.is_active).length}</span>
        </button>
      </div>
      {knowledgeView === "rules" && (
      <div className="knowledge-section" role="tabpanel">
        <div className="panel-title">
          <h2>判断规则</h2>
          <div className="panel-title-actions">
            <span>{rules.filter((rule) => rule.is_active).length} 条生效</span>
            <button
              type="button"
              disabled={
                !rules.some((rule) => rule.is_active) ||
                busy === "organize-knowledge"
              }
              onClick={() =>
                void act(
                  "organize-knowledge",
                  () => api("/knowledge/rules/organize", { method: "POST" }),
                  "AI 已完成规则抽象、去重和合并；原规则已保留为停用历史",
                )
              }
            >
              <Sparkles size={13} />
              {busy === "organize-knowledge" ? "正在整理…" : "AI 整理全部规则"}
            </button>
            <button type="button" onClick={() => setShowNewRule((value) => !value)}>
              <Plus size={13} /> 添加规则
            </button>
          </div>
        </div>
        {showNewRule && (
          <article className="knowledge-card compact-rule-editor">
            <label>
              类型
              <select value={newRuleType} onChange={(event) => setNewRuleType(event.target.value)}>
                {knowledgeTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <label title="global 表示全局；connector:类型或 source:编号表示限定信源；事件聚合也可填写消息分类">
              范围
              <input
                value={newRuleScope}
                placeholder="global"
                onChange={(event) => setNewRuleScope(event.target.value)}
              />
            </label>
            <label>规则<textarea value={newRuleText} onChange={(event) => setNewRuleText(event.target.value)} /></label>
            <div className="admin-actions">
              <button
                type="button"
                disabled={busy === "create-rule" || !newRuleScope.trim() || !newRuleText.trim()}
                onClick={() => void createRule()}
              >保存新规则</button>
              <button type="button" onClick={() => setShowNewRule(false)}>取消</button>
            </div>
          </article>
        )}
        <div className="rule-table-head">
          <span>类型</span>
          <span title="用于决定规则适用哪些消息；命中后才会传给 LLM">范围 ⓘ</span>
          <span>判断规则</span>
          <span>操作</span>
        </div>
        {visibleRules.map((rule) => (
          <EditableRule rule={rule} busy={busy} act={act} key={rule.id} />
        ))}
        <Pagination page={visibleRulePage} pageCount={rulePageCount} total={rules.length} onChange={setRulePage} />
      </div>
      )}
      {knowledgeView === "terms" && (
      <div className="knowledge-section" role="tabpanel">
        <div className="panel-title">
          <h2>翻译术语</h2>
          <div className="panel-title-actions">
            <span>{terms.filter((term) => term.is_active).length} 条生效</span>
            <button type="button" onClick={() => setShowNewTerm((value) => !value)}>
              <Plus size={13} /> 添加术语
            </button>
          </div>
        </div>
        {showNewTerm && (
          <article className="knowledge-card compact-term-editor">
            <label>原文术语<input value={newSourceTerm} onChange={(event) => setNewSourceTerm(event.target.value)} /></label>
            <label>标准译名<input value={newTranslation} onChange={(event) => setNewTranslation(event.target.value)} /></label>
            <div className="admin-actions">
              <button
                type="button"
                disabled={busy === "create-term" || !newSourceTerm.trim() || !newTranslation.trim()}
                onClick={() => void createTerm()}
              >保存新术语</button>
              <button type="button" onClick={() => setShowNewTerm(false)}>取消</button>
            </div>
          </article>
        )}
        <div className="term-table-head"><span>英文原词</span><span>标准译名</span><span>操作</span></div>
        {visibleTerms.map((term) => (
          <EditableTerm term={term} busy={busy} act={act} key={term.id} />
        ))}
        <Pagination page={visibleTermPage} pageCount={termPageCount} total={terms.length} onChange={setTermPage} />
      </div>
      )}
    </section>
  );
}

function EditableRule({
  rule,
  busy,
  act,
}: {
  rule: KnowledgeRule;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [knowledgeType, setKnowledgeType] = useState(rule.knowledge_type);
  const [scope, setScope] = useState(rule.scope);
  const [text, setText] = useState(rule.rule_text);
  return (
    <article className={`rule-row ${rule.is_active ? "" : "inactive"}`}>
      <select aria-label={`规则 ${rule.id} 类型`} value={knowledgeType} onChange={(event) => setKnowledgeType(event.target.value)}>
        {knowledgeTypes.map(([value, label]) => <option value={value} key={value}>{label}</option>)}
      </select>
      <input aria-label={`规则 ${rule.id} 范围`} value={scope} onChange={(event) => setScope(event.target.value)} />
      <textarea aria-label={`规则 ${rule.id} 正文`} value={text} onChange={(event) => setText(event.target.value)} />
      <div className="admin-actions">
        <button type="button" disabled={busy === `rule-${rule.id}` || !scope.trim() || !text.trim()} onClick={() => void act(
          `rule-${rule.id}`,
          () => api(`/knowledge/rules/${rule.id}`, {
            method: "PATCH",
            body: JSON.stringify({
              knowledge_type: knowledgeType,
              scope: scope.trim(),
              rule_text: text.trim(),
            }),
          }),
          "规则已更新",
        )}>保存</button>
        <button type="button" onClick={() => void act(
          `rule-toggle-${rule.id}`,
          () => api(`/knowledge/rules/${rule.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !rule.is_active }) }),
          rule.is_active ? "规则已停用" : "规则已启用",
        )}>{rule.is_active ? "停用" : "启用"}</button>
      </div>
    </article>
  );
}

function EditableTerm({
  term,
  busy,
  act,
}: {
  term: GlossaryTerm;
  busy: string | null;
  act: (key: string, action: () => Promise<unknown>, success: string) => Promise<void>;
}) {
  const [sourceTerm, setSourceTerm] = useState(term.source_term);
  const [translation, setTranslation] = useState(term.preferred_translation);
  return (
    <article className={`term-row ${term.is_active ? "" : "inactive"}`}>
      <input aria-label="英文原词" value={sourceTerm} onChange={(event) => setSourceTerm(event.target.value)} />
      <input aria-label="标准译名" value={translation} onChange={(event) => setTranslation(event.target.value)} />
      <div className="admin-actions">
        <button type="button" disabled={busy === `term-${term.id}` || !sourceTerm.trim() || !translation.trim()} onClick={() => void act(
          `term-${term.id}`,
          () => api(`/knowledge/glossary/${term.id}`, {
            method: "PATCH",
            body: JSON.stringify({
              source_term: sourceTerm.trim(),
              preferred_translation: translation.trim(),
            }),
          }),
          "术语已更新",
        )}>保存</button>
        <button type="button" onClick={() => void act(
          `term-toggle-${term.id}`,
          () => api(`/knowledge/glossary/${term.id}`, { method: "PATCH", body: JSON.stringify({ is_active: !term.is_active }) }),
          term.is_active ? "术语已停用" : "术语已启用",
        )}>{term.is_active ? "停用" : "启用"}</button>
      </div>
    </article>
  );
}

function TimeSortControl({
  value,
  onChange,
}: {
  value: TimeSort;
  onChange: (value: TimeSort) => void;
}) {
  return (
    <label className="time-sort-control">
      <span>消息时间</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value as TimeSort)}
      >
        <option value="desc">倒序（最新优先）</option>
        <option value="asc">顺序（最早优先）</option>
      </select>
    </label>
  );
}

function Pagination({
  page,
  pageCount,
  total,
  onChange,
}: {
  page: number;
  pageCount: number;
  total: number;
  onChange: (page: number) => void;
}) {
  if (pageCount <= 1) {
    return total ? <div className="pagination-summary">共 {total} 条</div> : null;
  }
  return (
    <div className="pagination">
      <span>共 {total} 条 · 第 {page}/{pageCount} 页</span>
      <div>
        <button type="button" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          <ChevronLeft size={14} /> 上一页
        </button>
        <button type="button" disabled={page >= pageCount} onClick={() => onChange(page + 1)}>
          下一页 <ChevronRight size={14} />
        </button>
      </div>
    </div>
  );
}
