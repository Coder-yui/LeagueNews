import { objectNumber, score } from "./admin-utils";

const factors = [["source", "来源", "source_reliability"], ["language", "措辞", "statement_certainty"], ["type", "类型", "content_type_prior"], ["timeliness", "时效", "staleness"]] as const;

export function CredibilityBreakdown({ scoreValue, components }: { scoreValue: number | null | undefined; components: Record<string, unknown> }) {
  return (
    <section className="admin-breakdown" aria-label="可信度四因子">
      <header><span>可信度</span><strong>{score(scoreValue)}</strong></header>
      {factors.map(([key, label, canonical]) => {
        const value = objectNumber(components[key] ?? components[canonical] ?? components[`${key}_factor`]);
        return <div key={key}><span>{label}</span><i><b style={{ width: `${Math.max(0, Math.min(1, value ?? 0)) * 100}%` }} /></i><em>{score(value)}</em></div>;
      })}
    </section>
  );
}
