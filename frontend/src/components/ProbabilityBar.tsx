export default function ProbabilityBar({ label, value, tone = "mint" }: { label: string; value: number; tone?: "mint" | "amber" | "coral" }) {
  return <div className="probability-row">
    <span>{label}</span>
    <div className="probability-track"><i className={`tone-${tone}`} style={{ transform: `scaleX(${Math.max(0, Math.min(1, value))})` }} /></div>
    <b>{(value * 100).toFixed(1)}%</b>
  </div>;
}

