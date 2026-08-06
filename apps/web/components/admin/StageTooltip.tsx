import { STAGE_LABELS, type StageView } from "./admin-utils";

export function StageTooltip({ stage }: { stage: StageView }) {
  return (
    <span className="admin-stage-tooltip" role="tooltip">
      <strong>{STAGE_LABELS[stage.name]}</strong>
      <span>{stage.detail ?? "暂无阶段摘要"}</span>
    </span>
  );
}
