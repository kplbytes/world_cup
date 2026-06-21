import React from "react";

export const AUTO_DAILY_OPEN_PARAMS = {
  with_ai: true,
  with_ensemble: true,
  auto_lock: true,
  only_missing: true,
  limit: 10,
  hours: 48,
  since_hours: 24,
} as const;

/** Render a small coloured dot indicator. */
export function statusDot(color: string): React.ReactElement {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        marginRight: 6,
        verticalAlign: "middle",
      }}
    />
  );
}

/** Format a duration in seconds to a human-readable string. */
export function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}分${s}秒`;
}
