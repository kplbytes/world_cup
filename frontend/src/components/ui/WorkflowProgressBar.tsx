import type { WorkflowProgress } from "../../types";

interface WorkflowProgressBarProps {
  progress?: WorkflowProgress | null;
  status?: string | null;
}

const STATUS_TEXT: Record<string, string> = {
  running: "运行中",
  success: "已完成",
  partial_success: "部分完成",
  failed: "失败",
};

export default function WorkflowProgressBar({ progress, status }: WorkflowProgressBarProps) {
  if (!progress) return null;
  const percent = Math.max(0, Math.min(100, progress.percent));
  const failedCount = progress.failed_steps?.length ?? 0;
  return (
    <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, fontSize: 12 }}>
        <span style={{ color: "var(--muted)" }}>运行进度</span>
        <span style={{ display: "inline-flex", gap: 6, color: failedCount ? "var(--coral)" : "var(--text)", fontVariantNumeric: "tabular-nums" }}>
          <span>{progress.completed_steps}/{progress.total_steps}</span>
          <span>{percent}%</span>
        </span>
      </div>
      <div
        aria-label="workflow progress"
        style={{
          height: 8,
          overflow: "hidden",
          background: "oklch(34% .015 250 / .25)",
          border: "1px solid var(--line)",
        }}
      >
        <div
          style={{
            width: `${percent}%`,
            height: "100%",
            background: failedCount ? "var(--coral)" : "var(--mint)",
            transition: "width 180ms ease",
          }}
        />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11, color: "var(--muted)" }}>
        <span>{progress.running_step ? progress.running_step : STATUS_TEXT[status ?? ""] ?? "等待执行"}</span>
        {failedCount > 0 ? <span style={{ color: "var(--coral)" }}>{failedCount} 个步骤失败</span> : null}
      </div>
    </div>
  );
}
