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

const VARIANT_STYLES: Record<string, { background: string; color: string }> = {
  primary: { background: "var(--mint)", color: "oklch(20% .04 160)" },
  danger: { background: "var(--coral)", color: "oklch(98% .01 95)" },
  warning: { background: "var(--amber)", color: "oklch(22% .04 80)" },
  success: { background: "var(--mint)", color: "oklch(20% .04 160)" },
};

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
  const variantStyle = VARIANT_STYLES[variant] ?? VARIANT_STYLES.primary;

  return (
    <div>
      <button
        disabled={isDisabled}
        onClick={onClick}
        className="app-button"
        data-variant={variant}
        style={{
          background: variantStyle.background,
          color: variantStyle.color,
        }}
      >
        {loading ? "运行中..." : label}
      </button>

      {!enabled && disabledReason && (
        <div style={{ fontSize: 11, color: "var(--coral)", marginTop: 6 }}>
          {disabledReason}
        </div>
      )}

      {enabled && estimatedCalls != null && estimatedCalls > 0 && (
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>
          预计调用 {estimatedCalls} 次模型
        </div>
      )}

      {warningText && (
        <div style={{ fontSize: 11, color: "var(--amber)", marginTop: 4 }}>
          {warningText}
        </div>
      )}
    </div>
  );
}
