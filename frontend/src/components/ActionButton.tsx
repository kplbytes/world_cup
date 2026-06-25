interface ActionButtonProps {
  label: string;
  enabled: boolean;
  disabledReason?: string;
  warningText?: string;
  loading?: boolean;
  progressPercent?: number | null;
  estimatedCalls?: number;
  onClick: () => void;
  variant?: "primary" | "danger" | "warning" | "success";
}

export default function ActionButton({
  label,
  enabled,
  disabledReason,
  warningText,
  loading,
  progressPercent,
  estimatedCalls,
  onClick,
  variant = "primary",
}: ActionButtonProps) {
  const isDisabled = !enabled || loading;
  const safePercent =
    typeof progressPercent === "number"
      ? Math.max(0, Math.min(100, Math.round(progressPercent)))
      : null;
  const buttonText = loading
    ? safePercent == null
      ? "运行中..."
      : `${label} ${safePercent}%`
    : label;

  return (
    <div>
      <button
        disabled={isDisabled}
        onClick={onClick}
        className={`app-button app-button--${variant}`}
      >
        {buttonText}
      </button>

      {loading && safePercent != null && (
        <div
          role="progressbar"
          aria-label={`${label}进度`}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={safePercent}
          style={{
            height: 4,
            marginTop: 6,
            overflow: "hidden",
            background: "oklch(34% .015 250 / .18)",
          }}
        >
          <div
            style={{
              width: `${safePercent}%`,
              height: "100%",
              background: "currentColor",
              transition: "width 180ms ease",
            }}
          />
        </div>
      )}

      {!enabled && disabledReason && (
        <div className="app-button__hint app-button__hint--error">
          {disabledReason}
        </div>
      )}

      {enabled && estimatedCalls != null && estimatedCalls > 0 && (
        <div className="app-button__hint">
          预计调用 {estimatedCalls} 次模型
        </div>
      )}

      {warningText && (
        <div className="app-button__hint app-button__hint--warn">
          {warningText}
        </div>
      )}
    </div>
  );
}
