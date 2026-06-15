import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getBacktestResults, getBacktestDataset, getRollingResults } from "../api";
import type { BacktestModelResult, RollingFoldResult, DrawMetricsResult, BootstrapCIResult } from "../types";
import SectionCard from "./ui/SectionCard";
import MetricCard from "./ui/MetricCard";
import EmptyState from "./ui/EmptyState";

// ─── Helpers ────────────────────────────────────────────────────────────

const MODEL_LABELS: Record<string, string> = {
  "legacy-elo-poisson": "Legacy Elo-Poisson (基线)",
  "refitted-elo-poisson": "Refitted Elo-Poisson",
  "dixon-coles": "Dixon-Coles",
  "neg-binomial": "Neg-Binomial",
  "multinomial-logistic": "Multinomial-Logistic",
};

function modelLabel(name: string): string {
  return MODEL_LABELS[name] ?? name;
}

/** Format a number or return placeholder when missing/undefined */
function fmt(v: number | undefined | null, decimals = 4): string {
  if (v === undefined || v === null || Number.isNaN(v)) return "N/A";
  return v.toFixed(decimals);
}

const METRIC_COLS: { key: keyof BacktestModelResult; label: string; fmt: (v: number) => string; lowerBetter: boolean }[] = [
  { key: "brier_score", label: "Brier", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "log_loss", label: "LogLoss", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "ece", label: "ECE", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "top1_hit_rate", label: "Top1", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "draw_recall", label: "DrawRecall", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "match_count", label: "N", fmt: (v) => String(v), lowerBetter: false },
];

const ROLLING_METRIC_COLS: { key: string; label: string; fmt: (v: number) => string; lowerBetter: boolean }[] = [
  { key: "brier_sum", label: "Brier", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "log_loss", label: "LogLoss", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "ece", label: "ECE", fmt: (v) => v.toFixed(4), lowerBetter: true },
  { key: "top1_hit_rate", label: "Top1", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "draw_recall", label: "DrawRecall", fmt: (v) => v.toFixed(4), lowerBetter: false },
  { key: "match_count", label: "N", fmt: (v) => String(v), lowerBetter: false },
];

const ADMISSION_REASONS: Record<string, string> = {
  shadow: "Brier优于基线 & LogLoss不劣于基线 & ECE不劣于基线",
  research: "Brier未显著劣于基线但有警告（未改善或Draw Recall未提升）",
  rejected: "Brier显著劣于基线 或 LogLoss劣于基线 或 ECE劣于基线",
  pending: "尚未完成准入评估",
};

const DRAW_METRIC_COLS: { key: keyof DrawMetricsResult; label: string }[] = [
  { key: "draw_brier", label: "Draw Brier" },
  { key: "draw_log_loss", label: "Draw LogLoss" },
  { key: "draw_ece", label: "Draw ECE" },
  { key: "draw_roc_auc", label: "Draw ROC-AUC" },
  { key: "draw_pr_auc", label: "Draw PR-AUC" },
  { key: "avg_p_draw_when_draw", label: "Avg P(draw|draw)" },
  { key: "avg_p_draw_when_not_draw", label: "Avg P(draw|not)" },
  { key: "top1_draw_recall", label: "Top1 DrawRecall" },
  { key: "n_draws", label: "N_draws" },
];

// ─── Component ──────────────────────────────────────────────────────────

export default function ModelResearch() {
  const [selectedFold, setSelectedFold] = useState<string>("cross_fold");

  const resultsQuery = useQuery({
    queryKey: ["backtest-results"],
    queryFn: getBacktestResults,
  });

  const datasetQuery = useQuery({
    queryKey: ["backtest-dataset"],
    queryFn: getBacktestDataset,
  });

  const rollingQuery = useQuery({
    queryKey: ["backtest-rolling"],
    queryFn: getRollingResults,
  });

  const isLoading = resultsQuery.isLoading || datasetQuery.isLoading || rollingQuery.isLoading;
  const hasError = resultsQuery.isError || datasetQuery.isError || rollingQuery.isError;

  if (isLoading) {
    return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>加载回测数据...</div>;
  }

  if (hasError) {
    return (
      <EmptyState title="无法加载回测数据">
        {String(resultsQuery.error ?? datasetQuery.error ?? rollingQuery.error ?? "未知错误")}
      </EmptyState>
    );
  }

  const results = resultsQuery.data!;
  const dataset = datasetQuery.data!;
  const rolling = rollingQuery.data;

  // Group by model name, then by split
  const byModel: Record<string, Record<string, BacktestModelResult>> = {};
  for (const r of results.models) {
    if (!byModel[r.model_name]) byModel[r.model_name] = {};
    byModel[r.model_name][r.split_name] = r;
  }

  const modelNames = Object.keys(byModel);
  const splits = ["train", "validation", "test"];

  // Check if there's any data
  const hasResults = results.models.length > 0;
  if (!hasResults) {
    return (
      <EmptyState title="暂无回测数据">
        请先运行回测以生成模型评估结果
      </EmptyState>
    );
  }

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
  const admissionReasonMap: Record<string, string> = {};
  for (const r of results.models) {
    admissionMap[r.model_name] = r.admission_status;
    if (r.admission_reason) {
      admissionReasonMap[r.model_name] = r.admission_reason;
    }
  }
  // Also check rolling admission_decisions
  if (rolling?.admission_decisions) {
    for (const [mn, status] of Object.entries(rolling.admission_decisions)) {
      if (!admissionMap[mn]) admissionMap[mn] = status;
    }
  }

  // Build fold options for selector
  const foldOptions: { value: string; label: string }[] = [];
  if (rolling && rolling.folds.length > 0) {
    for (const fold of rolling.folds) {
      foldOptions.push({ value: fold.fold_name, label: fold.fold_name });
    }
  }
  foldOptions.push({ value: "cross_fold", label: "跨折加权汇总" });
  foldOptions.push({ value: "audit_test_seen", label: "audit_test_seen" });

  // Get current fold data
  const currentFold = rolling?.folds.find(f => f.fold_name === selectedFold);
  const currentFoldData = selectedFold === "cross_fold"
    ? rolling?.cross_fold_summary
    : selectedFold === "audit_test_seen"
      ? null
      : currentFold?.model_metrics;

  // Get draw metrics for the selected fold
  const currentDrawMetrics = selectedFold === "cross_fold"
    ? null  // cross-fold draw metrics would need separate aggregation
    : selectedFold === "audit_test_seen"
      ? null
      : currentFold?.draw_metrics;

  // Get bootstrap results for the selected fold
  const currentBootstrap = selectedFold === "cross_fold"
    ? rolling?.oof_bootstrap
    : selectedFold === "audit_test_seen"
      ? null
      : currentFold?.bootstrap_results;

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

      {/* Canonical Brier formula */}
      <SectionCard title="Brier 公式说明">
        <p style={{ fontSize: 13, color: "var(--text-secondary)", fontFamily: "monospace" }}>
          Brier = mean(Σ(p_k - y_k)²) for k ∈ {"{H, D, A}"}
        </p>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 4 }}>
          其中 p_k 为预测概率，y_k 为实际结果（one-hot），k ∈ {"{H, D, A}"}。
          Brier (sum) = Σ(p_k - y_k)² per match，Brier (mean) = Brier (sum) / 3。
        </p>
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

      {/* Rolling-origin backtest section */}
      <SectionCard title="滚动原点回测 (Rolling-Origin)">
        <div style={{ marginBottom: 12, display: "flex", alignItems: "center", gap: 12 }}>
          <label style={{ fontSize: 13, color: "var(--text-secondary)" }}>选择折:</label>
          <select
            value={selectedFold}
            onChange={(e) => setSelectedFold(e.target.value)}
            style={{
              padding: "4px 8px",
              borderRadius: 4,
              border: "1px solid var(--line)",
              background: "var(--paper-2)",
              color: "var(--text-primary)",
              fontSize: 13,
            }}
          >
            {foldOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>

        {selectedFold === "audit_test_seen" ? (
          <div style={{ padding: 16, background: "rgba(250,204,21,0.08)", borderRadius: 6, border: "1px solid rgba(250,204,21,0.3)" }}>
            <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: 0 }}>
              <strong>audit_test_seen</strong> — 此固定测试集已被查看，不是未被触碰的数据。
              其结果仅作为审计参考，不可用于模型选择。
            </p>
            {results.audit_test_seen && (
              <div style={{ marginTop: 12, overflowX: "auto" }}>
                <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
                  <thead>
                    <tr>
                      <th style={thStyle}>模型</th>
                      <th style={thStyle}>Brier</th>
                      <th style={thStyle}>LogLoss</th>
                      <th style={thStyle}>ECE</th>
                      <th style={thStyle}>Top1</th>
                      <th style={thStyle}>DrawRecall</th>
                      <th style={thStyle}>N</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(results.audit_test_seen.models).map(([mn, m]) => (
                      <tr key={mn}>
                        <td style={tdStyle}>{modelLabel(mn)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{fmt(m.brier_sum)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{fmt(m.log_loss)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{fmt(m.ece)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{fmt(m.top1_hit_rate)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{fmt(m.draw_recall)}</td>
                        <td style={{ ...tdStyle, fontFamily: "monospace" }}>{m.match_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : currentFoldData && Object.keys(currentFoldData).length > 0 ? (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead>
                <tr>
                  <th style={thStyle}>模型</th>
                  {ROLLING_METRIC_COLS.map((c) => (
                    <th key={c.key} style={thStyle}>{c.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {Object.entries(currentFoldData).map(([modelKey, metricsRaw]) => {
                  const metrics = selectedFold === "cross_fold"
                    ? metricsRaw as Record<string, number>
                    : (metricsRaw as { eval: Record<string, number> }).eval;
                  return (
                    <tr key={modelKey}>
                      <td style={tdStyle}>{modelLabel(modelKey)}</td>
                      {ROLLING_METRIC_COLS.map((col) => {
                        const val = metrics[col.key];
                        return (
                          <td key={col.key} style={{ ...tdStyle, fontFamily: "monospace" }}>
                            {val !== undefined && val !== null ? col.fmt(val) : "N/A"}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : (
          <p style={{ fontSize: 13, color: "var(--text-secondary)" }}>暂无滚动回测数据</p>
        )}
      </SectionCard>

      {/* Draw reliability section */}
      <SectionCard title="平局可靠性评估 (Draw Reliability)">
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          平局（Draw）是足球预测中最难捕捉的结果。以下指标专门评估各模型对平局的预测能力。
        </p>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={thStyle}>模型</th>
                {DRAW_METRIC_COLS.map((c) => (
                  <th key={c.key} style={thStyle}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {modelNames.map((mn) => {
                // Try to get draw metrics from rolling fold data, then from backtest results
                const rollingDm = currentDrawMetrics?.[mn];
                const resultDm = byModel[mn]?.["test"]?.draw_metrics;

                // Build a merged draw metrics object
                const dm: Partial<DrawMetricsResult> = {};
                if (rollingDm) {
                  Object.assign(dm, rollingDm);
                } else if (resultDm) {
                  Object.assign(dm, resultDm);
                }

                const hasAnyDrawData = Object.keys(dm).length > 0;

                return (
                  <tr key={mn}>
                    <td style={tdStyle}>{modelLabel(mn)}</td>
                    {DRAW_METRIC_COLS.map((col) => {
                      const val = dm[col.key];
                      const isCountField = col.key === "n_draws";
                      return (
                        <td key={col.key} style={{ ...tdStyle, fontFamily: "monospace" }}>
                          {val !== undefined && val !== null
                            ? (isCountField ? String(val) : fmt(val))
                            : (hasAnyDrawData ? "N/A" : "—")
                          }
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

      {/* Bootstrap confidence intervals */}
      <SectionCard title="Bootstrap 显著性检验 (95% CI)">
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          配对 Bootstrap 检验（5000次迭代），比较各模型与 Legacy 基线的差异。CI 不包含 0 表示显著差异。
        </p>
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr>
                <th style={thStyle}>模型 vs Legacy</th>
                <th style={thStyle}>指标</th>
                <th style={thStyle}>观测差异</th>
                <th style={thStyle}>95% CI 下界</th>
                <th style={thStyle}>95% CI 上界</th>
                <th style={thStyle}>P(better)</th>
                <th style={thStyle}>结论</th>
              </tr>
            </thead>
            <tbody>
              {modelNames.filter(mn => mn !== "legacy-elo-poisson").map((mn) => {
                // Try to get bootstrap from rolling data
                const bsData = currentBootstrap?.[mn];
                const metrics = ["brier_sum", "log_loss", "top1_accuracy"];

                return metrics.map((metric) => {
                  const bsResult = bsData?.[metric];
                  return (
                    <tr key={`${mn}-${metric}`}>
                      <td style={tdStyle}>{modelLabel(mn)}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{metric}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{bsResult ? fmt(bsResult.observed_diff) : "N/A"}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{bsResult ? fmt(bsResult.ci_lower_95) : "N/A"}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{bsResult ? fmt(bsResult.ci_upper_95) : "N/A"}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{bsResult ? fmt(bsResult.p_better) : "N/A"}</td>
                      <td style={{ ...tdStyle, fontFamily: "monospace" }}>{bsResult?.conclusion ?? "N/A"}</td>
                    </tr>
                  );
                });
              })}
            </tbody>
          </table>
        </div>
      </SectionCard>

      {/* Admission summary */}
      <SectionCard title="准入决策">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {modelNames.map((mn) => {
            const status = admissionMap[mn] ?? "pending";
            const reason = admissionReasonMap[mn] || ADMISSION_REASONS[status];
            return (
              <div
                key={mn}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 4,
                  padding: "8px 14px",
                  borderRadius: 6,
                  border: "1px solid var(--line)",
                  background: "var(--paper-2)",
                  fontSize: 13,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <AdmissionBadge status={status} />
                  <span>{modelLabel(mn)}</span>
                </div>
                <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  {reason}
                </span>
              </div>
            );
          })}
        </div>
        <p style={{ fontSize: 12, color: "var(--text-secondary)", marginTop: 12 }}>
          准入规则：Shadow = Brier优于基线 & LogLoss不劣于基线(+0.01) & ECE不劣于基线(+0.01)。
          Research = Brier未显著劣于基线(+0.005)但有警告。Rejected = 违反任一硬性条件。
          Draw Recall 仅为诊断指标，非硬性准入门槛。
        </p>
      </SectionCard>
    </div>
  );
}

// ─── Sub-components ─────────────────────────────────────────────────────

function AdmissionBadge({ status }: { status: string }) {
  const cfg: Record<string, { bg: string; color: string; label: string }> = {
    shadow: { bg: "rgba(74,222,128,0.15)", color: "#4ade80", label: "Shadow" },
    research: { bg: "rgba(96,165,250,0.15)", color: "#60a5fa", label: "Research" },
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
