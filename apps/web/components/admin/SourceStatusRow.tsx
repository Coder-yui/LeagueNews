import type { CollectionSchedule, Source } from "@/lib/types";
import { relativeTime } from "./admin-utils";

export function SourceStatusRow({
  source,
  schedule,
  onEdit,
}: {
  source: Source;
  schedule?: CollectionSchedule;
  onEdit: () => void;
}) {
  const stale = !schedule?.last_success_at || Date.now() - new Date(schedule.last_success_at).getTime() > 7_200_000;
  return <tr>
    <td className="admin-number">#{source.id}</td>
    <td><strong>{source.name}</strong></td>
    <td><span className="admin-badge">{source.connector_type}</span></td>
    <td><span className={`admin-badge ${schedule?.enabled ? "success" : "subtle"}`}>{schedule?.enabled ? "计划已启用" : "计划未启用"}</span></td>
    <td>{schedule?.enabled ? `每 ${schedule.interval_minutes} 分钟` : "—"}</td>
    <td className={stale ? "admin-warning-text" : ""} title={schedule?.last_success_at ? new Date(schedule.last_success_at).toLocaleString("zh-CN") : "从未成功"}>{schedule?.last_success_at ? relativeTime(schedule.last_success_at) : "从未成功"}</td>
    <td><button type="button" className="admin-table-button" onClick={onEdit}>修改采集计划</button></td>
  </tr>;
}
