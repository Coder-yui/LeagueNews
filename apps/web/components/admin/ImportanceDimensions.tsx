import { objectNumber, score } from "./admin-utils";

const dimensions = [["scope", "范围"], ["magnitude", "幅度"], ["actionability", "行动"], ["duration", "持续"], ["novelty", "新颖"]] as const;

export function ImportanceDimensions({ scoreValue, dimensions: values }: { scoreValue: number | null | undefined; dimensions: Record<string, unknown> }) {
  return (
    <section className="admin-breakdown importance" aria-label="重要性五维">
      <header><span>重要性</span><strong>{score(scoreValue)}</strong></header>
      {dimensions.map(([key, label]) => {
        const aliases: Record<string, string> = { scope: "impact_scope", magnitude: "mag", actionability: "act", duration: "dur", novelty: "nov" };
        const value = objectNumber(values[key] ?? values[aliases[key]]);
        const normalized = value !== null && value > 1 ? value / 5 : value ?? 0;
        return <div key={key}><span>{label}</span><i><b style={{ width: `${Math.max(0, Math.min(1, normalized)) * 100}%` }} /></i><em>{score(value)}</em></div>;
      })}
    </section>
  );
}
