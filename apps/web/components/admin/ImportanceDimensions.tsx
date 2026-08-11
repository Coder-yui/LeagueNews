import { pointScore } from "./admin-utils";

const editorialDimensions = [
  ["importance_profile", "重要性档案"],
  ["scale", "内容规模"],
  ["audience_region", "适用范围"],
  ["competition_region", "赛事区域"],
  ["prominence", "对象知名度"],
  ["skin_tier", "皮肤档次"],
] as const;

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

export function ImportanceDimensions({ scoreValue, dimensions: values }: { scoreValue: number | null | undefined; dimensions: Record<string, unknown> }) {
  return (
    <section className="admin-breakdown importance" aria-label="重要性编辑特征">
      <header><span>重要性</span><strong>{pointScore(scoreValue)} / 100</strong></header>
      {editorialDimensions.map(([key, label]) => {
        const feature = objectValue(values[key]);
        return <div key={key}><span>{label}</span><em>{String(feature.value ?? "—")}</em></div>;
      })}
    </section>
  );
}
