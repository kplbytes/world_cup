type RiskLevel = "low" | "medium" | "high";

type RiskBadgeProps = {
  level: RiskLevel;
  label?: string;
};

const LABEL_MAP: Record<RiskLevel, string> = {
  low: "低风险",
  medium: "中风险",
  high: "高风险",
};

export default function RiskBadge({ level, label }: RiskBadgeProps) {
  return (
    <span className={`risk-badge risk-badge--${level}`}>
      {label ?? LABEL_MAP[level]}
    </span>
  );
}
