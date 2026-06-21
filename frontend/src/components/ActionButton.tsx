interface ActionButtonProps {
  label: string;
  enabled: boolean;
  disabledReason?: string;
  warningText?: string;
  loading?: boolean;
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
  estimatedCalls,
  onClick,
  variant = "primary",
}: ActionButtonProps) {
  const isDisabled = !enabled || loading;

  return (
    <div>
      <button
        disabled={isDisabled}
        onClick={onClick}
        className={`app-button app-button--${variant}`}
      >
        {loading ? "运行中..." : label}
      </button>

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
