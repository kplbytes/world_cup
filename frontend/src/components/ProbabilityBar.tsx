import { memo } from "react";

export default memo(function ProbabilityBar({ label, value, tone = "mint" }: { label: string; value: number; tone?: "mint" | "amber" | "coral" }) {
  if (value == null || isNaN(value)) {
    return <div className="probability-row">
      <span>{label}</span>
      <div className="probability-track"><i className={`tone-${tone}`} style={{ transform: "scaleX(0)" }} /></div>
      <b>N/A</b>
    </div>;
  }
  const clamped = Math.max(0, Math.min(1, value));
  return <div className="probability-row">
    <span>{label}</span>
    <div className="probability-track"><i className={`tone-${tone}`} style={{ transform: `scaleX(${clamped})` }} /></div>
    <b>{(clamped * 100).toFixed(1)}%</b>
  </div>;
});
