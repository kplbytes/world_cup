type MetricCardProps = {
  label: string;
  value: string | number;
  note?: string;
  tone?: "ok" | "warn" | "error" | "neutral";
};

const TONE_CLASS = {
  ok: "metric-card--ok",
  warn: "metric-card--warn",
  error: "metric-card--error",
  neutral: "",
};

export default function MetricCard({ label, value, note, tone = "neutral" }: MetricCardProps) {
  return (
    <div className={`metric-card ${TONE_CLASS[tone]}`}>
      <div className="metric-card__label">{label}</div>
      <div className="metric-card__value">{value}</div>
      {note && <div className="metric-card__note">{note}</div>}
    </div>
  );
}
