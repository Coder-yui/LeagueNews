import { StageTooltip } from "./StageTooltip";
import { STAGE_LABELS, type StageView } from "./admin-utils";

export function PipelineStageBar({ stages }: { stages: StageView[] }) {
  const completed = stages.filter((stage) => stage.status === "done" || stage.status === "skipped").length;
  return (
    <div className="admin-stage-wrap" aria-label={`处理进度 ${completed}/${stages.length}`}>
      <div className="admin-stage-bar">
        {stages.map((stage, index) => (
          <div className="admin-stage-segment" key={stage.name}>
            {index > 0 && <span className={`admin-stage-line ${stage.status}`} aria-hidden="true" />}
            <span className="admin-stage-node-wrap">
              <span
                className={`admin-stage-node ${stage.status}`}
                tabIndex={0}
                aria-label={`${STAGE_LABELS[stage.name]}：${stage.status}`}
              >
                {stage.status === "done" ? "✓" : stage.status === "failed" ? "×" : stage.status === "review" ? "Ⅱ" : ""}
              </span>
              <StageTooltip stage={stage} />
            </span>
          </div>
        ))}
      </div>
      <span className="admin-stage-count">{completed}/{stages.length}</span>
    </div>
  );
}
