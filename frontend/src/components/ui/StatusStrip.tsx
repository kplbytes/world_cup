export type StatusItem = {
  label: string;
  value: string;
  tone?: "ok" | "warn" | "error" | "neutral";
};

type StatusStripProps = {
  items: StatusItem[];
};

const TONE_MAP = {
  ok: "status-strip__value--ok",
  warn: "status-strip__value--warn",
  error: "status-strip__value--error",
  neutral: "status-strip__value--neutral",
};

export default function StatusStrip({ items }: StatusStripProps) {
  return (
    <div className="status-strip">
      {items.map((item, i) => (
        <span key={i} className="status-strip__item">
          <span className="status-strip__label">{item.label}</span>
          <span className={`status-strip__value ${TONE_MAP[item.tone ?? "neutral"]}`}>
            {item.value}
          </span>
        </span>
      ))}
    </div>
  );
}
