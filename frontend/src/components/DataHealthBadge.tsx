import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDataHealth } from "../api";
import type { DataHealthReport } from "../types";

type HealthTone = "ok" | "warn" | "error";

function computeTone(data: DataHealthReport): HealthTone {
  if (data.uses_real_data && data.mock_record_count === 0) return "ok";
  if (data.uses_real_data) return "warn";
  return "error";
}

const TONE_COLORS: Record<HealthTone, string> = {
  ok: "var(--success-green)",
  warn: "var(--accent-yellow)",
  error: "var(--risk-red)",
};

const TONE_LABELS: Record<HealthTone, string> = {
  ok: "真实数据",
  warn: "混合数据",
  error: "模拟数据",
};

function formatDateTime(iso: string | null): string {
  if (!iso) return "无";
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatDate(iso: string | null): string {
  if (!iso) return "无";
  try {
    const d = new Date(iso);
    return d.toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" });
  } catch {
    return iso;
  }
}

export default function DataHealthBadge() {
  const [open, setOpen] = useState(false);

  const query = useQuery({
    queryKey: ["data-health"],
    queryFn: getDataHealth,
    staleTime: 120_000,
  });

  const data = query.data;
  if (!data) {
    return (
      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-secondary)" }}>
        <span style={{ width: 8, height: 8, borderRadius: "50%", background: "var(--text-secondary)" }} />
        数据健康加载中
      </span>
    );
  }

  const tone = computeTone(data);
  const color = TONE_COLORS[tone];
  const label = TONE_LABELS[tone];

  return (
    <span style={{ position: "relative", display: "inline-flex", alignItems: "center" }}>
      <button
        onClick={() => setOpen(!open)}
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 6,
          fontSize: 11,
          color,
          background: "transparent",
          border: "1px solid",
          borderColor: color,
          padding: "2px 8px",
          borderRadius: 4,
          cursor: "pointer",
          fontWeight: 600,
          lineHeight: "18px",
        }}
      >
        <span style={{ width: 7, height: 7, borderRadius: "50%", background: color, flexShrink: 0 }} />
        {label}
      </button>

      {open && (
        <>
          <div
            style={{ position: "fixed", inset: 0, zIndex: 998 }}
            onClick={() => setOpen(false)}
          />
          <div
            style={{
              position: "absolute",
              top: "100%",
              right: 0,
              marginTop: 6,
              background: "oklch(22% .03 162)",
              border: "1px solid var(--card-border)",
              borderRadius: 6,
              padding: "10px 14px",
              fontSize: 11,
              lineHeight: 1.7,
              zIndex: 999,
              minWidth: 280,
              boxShadow: "0 8px 24px rgba(0,0,0,.4)",
              color: "var(--text-secondary)",
            }}
          >
            <div style={{ fontWeight: 700, color: "var(--ink)", marginBottom: 6, fontSize: 12 }}>数据健康详情</div>

            <div>使用真实数据：<span style={{ color, fontWeight: 600 }}>{data.uses_real_data ? "是" : "否"}</span></div>
            <div>可建模比赛数：<span style={{ color: "var(--ink)" }}>{data.total_historical_matches}</span></div>
            <div>日期覆盖：<span style={{ color: "var(--ink)" }}>{formatDate(data.time_coverage.earliest)} ~ {formatDate(data.time_coverage.latest)}</span></div>
            <div>国家队覆盖：<span style={{ color: "var(--ink)" }}>{data.national_team_coverage.teams_with_data}/{data.national_team_coverage.total_teams} ({(data.national_team_coverage.coverage_rate * 100).toFixed(0)}%)</span></div>
            <div>仅日期精度：<span style={{ color: "var(--ink)" }}>{data.date_only_count}</span></div>
            <div>未映射队伍：<span style={{ color: data.unmapped_team_count > 0 ? "var(--accent-yellow)" : "var(--ink)" }}>{data.unmapped_team_count}</span></div>
            <div>排除加时/点球：<span style={{ color: "var(--ink)" }}>{data.excluded_extra_time_count}</span></div>
            <div>Mock 记录：<span style={{ color: data.mock_record_count > 0 ? "var(--accent-yellow)" : "var(--success-green)" }}>{data.mock_record_count}</span></div>
            <div>Mock 档案：<span style={{ color: data.mock_profile_count > 0 ? "var(--accent-yellow)" : "var(--success-green)" }}>{data.mock_profile_count}</span></div>
            <div style={{ borderTop: "1px solid var(--card-border)", marginTop: 6, paddingTop: 6 }}>
              最后更新：<span style={{ color: "var(--ink)" }}>{formatDateTime(data.last_update)}</span>
            </div>
          </div>
        </>
      )}
    </span>
  );
}
