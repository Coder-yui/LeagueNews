import { objectNumber, score } from "./admin-utils";

const legacyDimensions = [["scope", "范围"], ["magnitude", "幅度"], ["actionability", "行动"], ["duration", "持续"], ["novelty", "新颖"]] as const;
const editorialDimensions = [
  ["editorial_subtype", "编辑类型"],
  ["scale", "内容规模"],
  ["audience_region", "适用范围"],
  ["competition_region", "赛事区域"],
  ["prominence", "对象知名度"],
  ["skin_tier", "皮肤档次"],
  ["information_value", "信息增量"],
] as const;

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function ImportanceDimensions({ scoreValue, dimensions: values }: { scoreValue: number | null | undefined; dimensions: Record<string, unknown> }) {
  if ("editorial_subtype" in values) {
    return (
      <section className="admin-breakdown importance" aria-label="重要性编辑特征">
        <header><span>重要性</span><strong>{score(scoreValue)}</strong></header>
        {editorialDimensions.map(([key, label]) => {
          const feature = objectValue(values[key]);
          return <div key={key}><span>{label}</span><em>{String(feature.value ?? "—")}</em></div>;
        })}
      </section>
    );
  }
  return (
    <section className="admin-breakdown importance" aria-label="旧版重要性维度">
      <header><span>重要性</span><strong>{score(scoreValue)}</strong></header>
      {legacyDimensions.map(([key, label]) => {
        const aliases: Record<string, string> = { scope: "impact_scope", magnitude: "mag", actionability: "act", duration: "dur", novelty: "nov" };
        const value = objectNumber(values[key] ?? values[aliases[key]]);
        const normalized = value !== null && value > 1 ? value / 5 : value ?? 0;
        return <div key={key}><span>{label}</span><i><b style={{ width: `${Math.max(0, Math.min(1, normalized)) * 100}%` }} /></i><em>{score(value)}</em></div>;
      })}
    </section>
  );
}
