import { useQuery } from "@tanstack/react-query";
import { getBacktestResults, getBacktestDataset } from "../api";
import type { BacktestModelResult } from "../types";
import SectionCard from "./ui/SectionCard";
import MetricCard from "./ui/MetricCard";
import EmptyState from "./ui/EmptyState";

// ─── Helpers ────────────────────────────────────────────────────────────

const MODEL_LABELS: Record<string, string> = {
  "legacy-elo-poisson": "Legacy Elo-Poisson (基线)",
  "refitted-elo-poisson": "Refitted Elo-Poisson",
  "dixon-coles": "Dixon-Coles",
  "neg-binomial": "Neg-Binomial",
};

function modelLabel(name: string): string {
  return MODEL_LABELS[name] ?? name;
}

const METRIC_COLS: { key: keyof BacktestModelResult; label: string; fmt: (v: number) => string; lowerBetter: boolean }[] = [
  { key: "brier_score", label: "Brier", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "log_loss", label: "LogLoss", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "ece", label: "ECE", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "top1_hit_rate", label: "Top1", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "draw_recall", label: "DrawRecall", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "match_count", label: "N", fmt: (v) => String(v), lowerBetter: false },
];

// ─── Component ──────────────────────────────────────────────────────────

export default function ModelResearch() {
  const resultsQuery = useQuery({
    queryKey: ["backtest-results"],
    queryFn: getBacktestResults,
  });

  const datasetQuery = useQuery({
    queryKey: ["backtest-dataset"],
    queryFn: getBacktestDataset,
  });

  if (resultsQuery.isLoading || datasetQuery.isLoading) {
    return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>加载回测数据...</div>;
  }

  if (resultsQuery.isError || datasetQuery.isError) {
    return (
      <EmptyState
        title="无法加载回测数据"
      >
        {String(resultsQuery.error ?? datasetQuery.error ?? "未知错误")}
      </EmptyState>
    );
  }

  const results = resultsQuery.data!;
  const dataset = datasetQuery.data!;

  // Group by model name, then by split
  const byModel: Record<string, Record<string, BacktestModelResult>> = {};
  for (const r of results.models) {
    if (!byModel[r.model_name]) byModel[r.model_name] = {};
    byModel[r.model_name][r.split_name] = r;
  }

  const modelNames = Object.keys(byModel);
  const splits = ["train", "validation", "test"];

  // Compute best values per metric per split for highlighting
  const bestBySplit: Record<string, Record<string, { value: number; model: string }>> = {};
  for (const split of splits) {
    bestBySplit[split] = {};
    for (const col of METRIC_COLS) {
      if (col.key === "match_count") continue;
      let best: { value: number; model: string } | null = null;
      for (const mn of modelNames) {
        const r = byModel[mn]?.[split];
        if (!r) continue;
        const v = r[col.key] as number;
        if (best === null || (col.lowerBetter ? v < best.value : v > best.value)) {
          best = { value: v, model: mn };
        }
      }
      if (best) bestBySplit[split][col.key] = best;
    }
  }

  // Admission map (from any split record)
  const admissionMap: Record<string, string> = {};
  for (const r of results.models) {
    admissionMap[r.model_name] = r.admission_status;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Header */}
      <SectionCard title="回测模型研究" badge={results.data_version ?? "—"}>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          <MetricCard label="数据版本" value={results.data_version ?? "—"} />
          <MetricCard label="总比赛数" value={dataset.total_matches} />
          <MetricCard label="训练集" value={dataset.splits.train.match_count} note={`${dataset.splits.train.start.slice(0, 10)} ~ ${dataset.splits.train.end.slice(0, 10)}`} />
          <MetricCard label="验证集" value={dataset.splits.validation.match_count} note={`${dataset.splits.validation.start.slice(0, 10)} ~ ${dataset.splits.validation.end.slice(0, 10)}`} />
          <MetricCard label="测试集" value={dataset.splits.test.match_count} note={`${dataset.splits.test.start.slice(0, 10)} ~ ${dataset.splits.test.end.slice(0, 10)}`} />
          <MetricCard label="排除WC2026" value={dataset.excluded_wc_2026} />
        </div>
      </SectionCard>

      {/* Per-split tables */}
      {splits.map((split) => (
        <SectionCard key={split} title={`${split === "train" ? "训练集" : split === "validation" ? "验证集" : "测试集"}指标`}>
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={thStyle}>模型</th>
                  <th style={thStyle}>准入</th>
                  {METRIC_COLS.map((c) => (
                    <th key={c.key} style={thStyle}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {modelNames.map((mn) => {
                  const r = byModel[mn]?.[split];
                  if (!r) return null;
                  const admission = admissionMap[mn] ?? "pending";
                  return (
                    <tr key={mn}>
                      <td style={tdStyle}>{modelLabel(mn)}</td>
                      <td style={tdStyle}>
                        <AdmissionBadge status={admission} />
                      </td>
                      {METRIC_COLS.map((col) => {
                        const val = r[col.key] as number;
                        const best = bestBySplit[split]?.[col.key];
                        const isBest = best && best.model === mn && best.value === val;
                        return (
                          <td
                            key={col.key}
                            style={{
                              ...tdStyle,
                              fontFamily: "monospace",
                              fontWeight: isBest ? 700 : 400,
                              color: isBest ? "var(--accent-green, #4ade80)" : "var(--text-primary)",
                            }}
                          >
                            {col.fmt(val)}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </SectionCard>
      ))}

      {/* Admission summary */}
      <SectionCard title="准入决策">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {modelNames.map((mn) => (
            <div
              key={mn}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                padding: "8px 14px",
                borderRadius: 6,
                border: "1px solid var(--line)",
                background: "var(--paper-2)",
                fontSize: 13,
              }}
            >
              <AdmissionBadge status={admissionMap[mn] ?? "pending"} />
              <span>{modelLabel(mn)}</span>
            </div>
          ))}
        </div>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 12 }}>
          准入规则：Shadow = Brier优于基线 & LogLoss不劣于基线 & 平局召回率提升&gt;0.05 & ECE不劣于基线。不满足任一条件则 Rejected。
        </p>
      </SectionCard>
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────

function AdmissionBadge({ status }: { status: string }) {
  const cfg: Record<string, { bg: string; color: string; label: string }> = {
    shadow: { bg: "rgba(74,222,128,0.15)", color: "#4ade80", label: "Shadow" },
    rejected: { bg: "rgba(248,113,113,0.15)", color: "#f87171", label: "Rejected" },
    pending: { bg: "rgba(250,204,21,0.15)", color: "#facc15", label: "Pending" },
  };
  const c = cfg[status] ?? cfg.pending;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        background: c.bg,
        color: c.color,
      }}
    >
      {c.label}
    </span>
  );
}

// ─── Styles ─────────────────────────────────────────────────────────────

const thStyle: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 10px",
  borderBottom: "2px solid var(--line)",
  color: "var(--text-secondary)",
  fontWeight: 600,
  fontSize: 12,
  whiteSpace: "nowrap",
};

const tdStyle: React.CSSProperties = {
  padding: "8px 10px",
  borderBottom: "1px solid var(--line)",
  whiteSpace: "nowrap",
};
