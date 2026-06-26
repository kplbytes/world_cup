import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getAccuracyCommandCenter, getAIEvaluation, getModelScoreDetails, getProfileEvaluation, getMatchCountBreakdown, getErrorAttributionSummary, getModelScoreByVersion, getAdaptiveWeights } from "../api";
import { formatChinaTimeShort } from "../utils/time";
import { getTeamDisplayNameFromAny } from "../utils/teamNames";
import { fmt, pct } from "../utils/format";
import type { MatchScoreDetailItem, VersionScoreSummary, MatchCountBreakdown, ErrorAttributionSummary, ShadowModelRow, ModelComparisonItem } from "../types";
import SectionCard from "./ui/SectionCard";
import MetricCard from "./ui/MetricCard";
import EmptyState from "./ui/EmptyState";

// ─── Exclusion reason code mapping ──────────────────────────────────
const EXCLUSION_REASON_MAP: Record<string, string> = {
  no_pre_match_snapshot: "无赛前预测快照",
  no_prediction: "无预测记录",
  no_final_score: "无终场比分",
  excluded_after_kickoff: "开赛后生成预测",
  ai_missing: "AI预测缺失",
  ensemble_missing: "集成预测缺失",
};

function translateExclusionReason(reason: string): string {
  return EXCLUSION_REASON_MAP[reason] ?? reason;
}

// ─── Section: 评分排除说明 ────────────────────────────────────────────
function ScoringExclusions({ exclusions }: { exclusions: Array<{ match_id: string; home_team: string; away_team: string; reason: string }> }) {
  if (!exclusions || exclusions.length === 0) return null;

  return (
    <section className="accuracy-section">
      <h3>未参与评分的比赛</h3>
      <div
        style={{
          color: "var(--amber)",
          fontSize: "12px",
          marginBottom: "10px",
          padding: "8px 12px",
          border: "1px solid var(--amber)",
          borderRadius: "4px",
          background: "oklch(34% .025 80 / .1)",
        }}
      >
        以下已结束比赛未纳入模型评分，原因已标注
      </div>
      <div style={{ background: "var(--paper-2)", border: "1px solid var(--line)", borderRadius: "4px" }}>
        {exclusions.map((e) => {
          const homeZh = getTeamDisplayNameFromAny(e.home_team);
          const awayZh = getTeamDisplayNameFromAny(e.away_team);
          return (
            <div
              key={e.match_id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                padding: "8px 12px",
                borderBottom: "1px solid var(--line)",
                fontSize: "12px",
              }}
            >
              <span style={{ fontWeight: 600 }}>{homeZh} vs {awayZh}</span>
              <span style={{ color: "var(--coral)", fontSize: "11px" }}>{translateExclusionReason(e.reason)}</span>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ─── Section A: 当前结论 ────────────────────────────────────────────
function CurrentConclusion({
  sampleCount,
  insufficientSample,
  recommendation,
  matchCountBreakdown,
}: {
  sampleCount: number;
  insufficientSample: boolean;
  recommendation: { recommended_model_version: string; confidence: string; reason: string; fallback_model_version: string; sample_warning?: string; brier_improvement?: number; relative_improvement?: number } | null;
  matchCountBreakdown: MatchCountBreakdown | null;
}) {
  return (
    <section className="accuracy-section">
      <h3>当前结论</h3>
      <div
        style={{
          padding: "20px",
          borderRadius: "6px",
          background: insufficientSample ? "oklch(34% .05 80 / .12)" : "oklch(34% .05 150 / .1)",
          borderLeft: `4px solid ${insufficientSample ? "var(--amber)" : "var(--mint)"}`,
        }}
      >
        {/* Match count breakdown stats */}
        {matchCountBreakdown && (
          <div style={{ display: "flex", gap: "10px", marginBottom: "14px", flexWrap: "wrap" }}>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>已完赛: </span>
              <strong style={{ color: "var(--ink)" }}>{matchCountBreakdown.total_finished}</strong>
            </div>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>有赛前预测: </span>
              <strong style={{ color: "var(--ink)" }}>{matchCountBreakdown.has_pre_match_prediction}</strong>
            </div>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>有开球前快照: </span>
              <strong style={{ color: "var(--ink)" }}>{matchCountBreakdown.has_pre_kickoff_snapshot}</strong>
            </div>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>实际评分: </span>
              <strong style={{ color: insufficientSample ? "var(--coral)" : "var(--mint)" }}>{matchCountBreakdown.actually_scored}</strong>
            </div>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>未评分: </span>
              <strong style={{ color: matchCountBreakdown.missing_snapshot > 0 ? "var(--coral)" : "var(--muted)" }}>{matchCountBreakdown.missing_snapshot}</strong>
            </div>
          </div>
        )}

        <div style={{ display: "flex", gap: "16px", marginBottom: "14px", flexWrap: "wrap" }}>
          <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
            <span style={{ color: "var(--muted)" }}>样本数: </span>
            <strong style={{ color: insufficientSample ? "var(--coral)" : "var(--mint)" }}>{sampleCount}</strong>
          </div>
          <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
            <span style={{ color: "var(--muted)" }}>样本: </span>
            <strong style={{ color: insufficientSample ? "var(--coral)" : "var(--mint)" }}>
              {insufficientSample ? "不足 (需 ≥5)" : "充分"}
            </strong>
          </div>
        </div>

        {insufficientSample ? (
          <>
            <div style={{ fontSize: "18px", fontWeight: 700, color: "var(--amber)", marginBottom: "8px" }}>
              {matchCountBreakdown
                ? `已完赛 ${matchCountBreakdown.total_finished} 场，其中有效评分 ${matchCountBreakdown.actually_scored} 场。当前样本不足，不建议调整模型。`
                : "样本不足，暂不判断哪个模型更准"}
            </div>
            <div style={{ fontSize: "15px", color: "var(--ink)", marginBottom: "10px" }}>
              默认使用：<strong style={{ color: "var(--mint)" }}>baseline</strong>
            </div>
            {recommendation && (
              <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "8px" }}>
                <span>系统推荐: {recommendation.recommended_model_version}（样本充足后生效）</span>
                {recommendation.sample_warning && (
                  <span style={{ color: "var(--coral)", marginLeft: "8px" }}>⚠️ {recommendation.sample_warning}</span>
                )}
              </div>
            )}
          </>
        ) : (
          <>
            {recommendation ? (
              <>
                <div style={{ fontSize: "18px", fontWeight: 700, marginBottom: "8px" }}>
                  推荐模型：<strong style={{ color: "var(--mint)" }}>{recommendation.recommended_model_version}</strong>
                </div>
                <div style={{ fontSize: "13px", color: "var(--muted)", marginBottom: "8px" }}>
                  {recommendation.reason}
                </div>
                <div style={{ display: "flex", gap: "14px", fontSize: "12px", color: "var(--muted)", flexWrap: "wrap" }}>
                  <span>置信度: <strong style={{ color: "var(--ink)" }}>{recommendation.confidence}</strong></span>
                  {recommendation.brier_improvement != null && (
                    <span>Brier 改善: <strong style={{ color: "var(--mint)" }}>{fmt(recommendation.brier_improvement)}</strong></span>
                  )}
                  {recommendation.relative_improvement != null && (
                    <span>相对改善: <strong style={{ color: "var(--mint)" }}>{pct(recommendation.relative_improvement)}</strong></span>
                  )}
                  <span>备用: {recommendation.fallback_model_version}</span>
                </div>
              </>
            ) : (
              <div style={{ fontSize: "14px", color: "var(--muted)" }}>暂无推荐数据</div>
            )}
          </>
        )}
        <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "12px", padding: "6px 10px", borderLeft: "2px solid var(--line)" }}>
          赛后评分使用开赛前最后一份有效预测快照。三分类 Brier 随机基线 ≈ 0.667，当前样本不足时不建议调整模型。
        </div>
      </div>
    </section>
  );
}

// ─── Section B: 模型对比 ────────────────────────────────────────────
function ModelComparison({ versions, insufficientSample }: { versions: VersionScoreSummary[]; insufficientSample: boolean }) {
  if (!versions.length) return <div className="empty">暂无模型评分数据</div>;

  const bestBrier = Math.min(...versions.map((v) => v.brier));

  return (
    <section className="accuracy-section">
      <h3>模型对比</h3>
      {insufficientSample && (
        <div
          style={{
            color: "var(--amber)", fontSize: "12px", marginBottom: "10px", padding: "8px 12px",
            border: "1px solid var(--amber)", borderRadius: "4px", background: "oklch(34% .025 80 / .1)",
          }}
        >
          ⚠️ 样本不足，以下对比仅供参考
        </div>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>model_version</th>
              <th>样本</th>
              <th>命中率</th>
              <th>Brier</th>
              <th>LogLoss</th>
              <th>是否建议使用</th>
              <th>说明</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => {
              const isBest = v.brier === bestBrier;
              const recommended = isBest && !insufficientSample;
              const notes: string[] = [];
              if (v.draw_miss_count > 0) notes.push(`平局漏判${v.draw_miss_count}场`);
              if (v.favorite_overestimated_count > 0) notes.push(`强队高估${v.favorite_overestimated_count}场`);
              if (v.overconfident_wrong_count > 0) notes.push(`过度自信${v.overconfident_wrong_count}场`);

              return (
                <tr key={v.model_version}>
                  <td className="version-cell">{v.model_version}</td>
                  <td>{v.sample_count}</td>
                  <td>{pct(v.hit_rate)}</td>
                  <td className={v.brier <= 0.3 ? "good" : v.brier >= 0.5 ? "bad" : ""}>{fmt(v.brier)}</td>
                  <td>{fmt(v.logloss)}</td>
                  <td>
                    {recommended ? (
                      <span style={{ color: "var(--mint)", fontWeight: 600 }}>✅ 推荐</span>
                    ) : isBest && insufficientSample ? (
                      <span style={{ color: "var(--amber)" }}>⏳ 待定</span>
                    ) : (
                      <span style={{ color: "var(--muted)" }}>—</span>
                    )}
                  </td>
                  <td style={{ fontSize: "11px", color: "var(--muted)", maxWidth: "200px" }}>
                    {notes.length > 0 ? notes.join("；") : "—"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div style={{ fontSize: "11px", color: "var(--muted)", marginTop: "8px", padding: "6px 10px", borderLeft: "2px solid var(--line)" }}>
          三分类 Brier 随机基线 ≈ 0.667（非 0.25）。Brier 越低越好，0 = 完美预测。
        </div>
      </div>
    </section>
  );
}

// ─── Section C: AI 对比 ─────────────────────────────────────────────
function AIComparison({
  aiEvaluation,
  insufficientSample,
}: {
  aiEvaluation: {
    system?: { sample_count: number; brier: number | null; logloss: number | null; hit_rate: number | null };
    ai_by_version: Record<string, { sample_count: number; brier: number | null; logloss: number | null; hit_rate: number | null; helped: number; hurt: number }>;
    ai_effect?: Record<string, { effect: string; brier_diff: number }>;
  } | null;
  insufficientSample: boolean;
}) {
  if (!aiEvaluation) return <div className="empty">暂无 AI 评估数据</div>;

  const aiByVersion = aiEvaluation.ai_by_version ?? {};
  const aiEffect = aiEvaluation.ai_effect ?? {};

  return (
    <section className="accuracy-section">
      <h3>AI 对比</h3>
      {insufficientSample && (
        <div
          style={{
            color: "var(--amber)", fontSize: "12px", marginBottom: "10px", padding: "8px 12px",
            border: "1px solid var(--amber)", borderRadius: "4px", background: "oklch(34% .025 80 / .1)",
          }}
        >
          ⚠️ 样本不足，AI 效果评估可能不可靠
        </div>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>模型</th>
              <th>评分样本</th>
              <th>优于基线</th>
              <th>劣于基线</th>
              <th>Brier</th>
              <th>LogLoss</th>
              <th>是否有帮助</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(aiByVersion).map(([version, data]) => {
              const effect = aiEffect[version];
              return (
                <tr key={version}>
                  <td className="version-cell">{version}</td>
                  <td>{data.sample_count}</td>
                  <td className="good">{data.helped}</td>
                  <td className="bad">{data.hurt}</td>
                  <td>{data.brier != null ? fmt(data.brier) : "—"}</td>
                  <td>{data.logloss != null ? fmt(data.logloss) : "—"}</td>
                  <td className={effect?.effect === "helped" ? "good" : effect?.effect === "hurt" ? "bad" : ""}>
                    {effect?.effect === "helped" ? "✅ 有帮助" : effect?.effect === "hurt" ? "❌ 有损害" : "➖ 中性"}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─── Section D: 错误归因 ────────────────────────────────────────────
function ErrorAttribution({ versions, errorSummary }: { versions: VersionScoreSummary[]; errorSummary: ErrorAttributionSummary | null }) {
  if (!versions.length && !errorSummary) return null;

  const latest = versions[0];
  const patterns = [
    { label: "平局低估", count: latest?.draw_miss_count ?? 0, color: "var(--amber)" },
    { label: "强队高估", count: latest?.favorite_overestimated_count ?? 0, color: "var(--coral)" },
    { label: "冷门漏判", count: latest?.underdog_underestimated_count ?? 0, color: "var(--coral)" },
    { label: "过度自信", count: latest?.overconfident_wrong_count ?? 0, color: "var(--coral)" },
  ];

  return (
    <section className="accuracy-section">
      <h3>错误归因</h3>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        {patterns.map((p) => (
          <span
            key={p.label}
            style={{
              padding: "6px 14px",
              borderRadius: "4px",
              fontSize: "12px",
              fontWeight: 600,
              background: p.count > 0 ? "oklch(34% .05 25 / .12)" : "oklch(34% .025 160 / .1)",
              borderLeft: `3px solid ${p.count > 0 ? p.color : "var(--line)"}`,
              color: p.count > 0 ? p.color : "var(--muted)",
            }}
          >
            {p.label}{" "}
            <span style={{ fontSize: "14px", fontWeight: 700 }}>{p.count}</span>
          </span>
        ))}
      </div>

      {/* Error attribution summary from API */}
      {errorSummary && (
        <div style={{ marginTop: "12px", display: "flex", gap: "8px", flexWrap: "wrap" }}>
          {[
            { label: "平局低估", count: errorSummary.draw_underestimated, color: "var(--amber)" },
            { label: "强队高估", count: errorSummary.favorite_overestimated, color: "var(--coral)" },
            { label: "冷门漏判", count: errorSummary.underdog_underestimated, color: "var(--coral)" },
            { label: "过度自信", count: errorSummary.overconfident_wrong, color: "var(--coral)" },
            { label: "低分平局漏判", count: errorSummary.low_score_draw_missed, color: "var(--amber)" },
            { label: "Market缺失", count: errorSummary.market_missing, color: "var(--muted)" },
            { label: "AI缺失", count: errorSummary.ai_missing, color: "var(--muted)" },
            { label: "集成有帮助", count: errorSummary.ensemble_helped, color: "var(--mint)" },
            { label: "集成有损害", count: errorSummary.ensemble_hurt, color: "var(--coral)" },
          ].map((p) => (
            <span
              key={p.label}
              style={{
                padding: "6px 14px",
                borderRadius: "4px",
                fontSize: "12px",
                fontWeight: 600,
                background: p.count > 0 ? "oklch(34% .05 25 / .12)" : "oklch(34% .025 160 / .1)",
                borderLeft: `3px solid ${p.count > 0 ? p.color : "var(--line)"}`,
                color: p.count > 0 ? p.color : "var(--muted)",
              }}
            >
              {p.label}{" "}
              <span style={{ fontSize: "14px", fontWeight: 700 }}>{p.count}</span>
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

// ─── Section E: 历史比赛复盘 ────────────────────────────────────────
function MatchReviewItem({ matchId, models }: { matchId: string; models: MatchScoreDetailItem[] }) {
  const [expanded, setExpanded] = useState(false);
  const first = models[0];

  const homeZh = getTeamDisplayNameFromAny(first.home_team);
  const awayZh = getTeamDisplayNameFromAny(first.away_team);

  const resultLabel = first.actual_result === "home" ? `${homeZh}胜` : first.actual_result === "draw" ? "平局" : `${awayZh}胜`;

  // Summary: best model hit rate across models
  const anyHit = models.some((m) => m.outcome_hit);
  const modelCount = models.length;

  return (
    <div
      style={{
        borderBottom: "1px solid var(--line)",
        cursor: "pointer",
      }}
      onClick={() => setExpanded(!expanded)}
    >
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(120px, 1fr) auto minmax(120px, 1fr) 60px 50px 40px",
          gap: "10px",
          alignItems: "center",
          padding: "12px 8px",
        }}
      >
        <span style={{ fontWeight: 600, fontSize: "13px" }}>{homeZh}</span>
        <span style={{ color: "var(--amber)", fontWeight: 600, fontSize: "12px" }}>vs</span>
        <span style={{ fontWeight: 600, fontSize: "13px", textAlign: "right" }}>{awayZh}</span>
        <span style={{ fontSize: "12px", color: "var(--muted)", textAlign: "center" }}>{resultLabel}</span>
        <span style={{ fontSize: "11px", color: "var(--muted)", textAlign: "center" }}>{modelCount}模型</span>
        <span style={{ fontSize: "14px", color: "var(--muted)", textAlign: "center" }}>{expanded ? "▲" : "▼"}</span>
      </div>

      {expanded && (
        <div style={{ padding: "0 8px 16px", fontSize: "12px" }}>
          <div style={{ marginBottom: "8px", color: "var(--muted)", fontSize: "11px" }}>
            时间: {formatChinaTimeShort(first.kickoff)}
          </div>
          {/* Per-model breakdown */}
          {models.map((m) => {
            const versionLabel = m.model_version.replace("elo-poisson-v1", "系统").replace("ai-", "AI:");
            return (
              <div key={`${matchId}-${m.model_version}`} style={{ padding: "8px 10px", marginBottom: "6px", borderRadius: "4px", background: "oklch(34% .015 260 / .06)", border: "1px solid var(--line)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "6px" }}>
                  <span style={{ fontWeight: 600, fontSize: "11px" }}>{versionLabel}</span>
                  <span className={m.outcome_hit ? "good" : "bad"} style={{ fontWeight: 600, fontSize: "11px" }}>
                    {m.outcome_hit ? "✅ 命中" : "❌ 未中"}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginBottom: "4px" }}>
                  <span style={{ padding: "2px 6px", borderRadius: "3px", background: "oklch(34% .025 160 / .2)", fontSize: "10px" }}>
                    主 {pct(m.home_win_prob)}
                  </span>
                  <span style={{ padding: "2px 6px", borderRadius: "3px", background: "oklch(34% .025 160 / .2)", fontSize: "10px" }}>
                    平 {pct(m.draw_prob)}
                  </span>
                  <span style={{ padding: "2px 6px", borderRadius: "3px", background: "oklch(34% .025 160 / .2)", fontSize: "10px" }}>
                    客 {pct(m.away_win_prob)}
                  </span>
                </div>
                <div style={{ display: "flex", gap: "12px", color: "var(--muted)", fontSize: "10px" }}>
                  <span>Brier: {fmt(m.brier)}</span>
                  <span>LogLoss: {fmt(m.logloss)}</span>
                </div>
                {m.error_types?.length > 0 && (
                  <div style={{ marginTop: "4px", display: "flex", gap: "4px", flexWrap: "wrap" }}>
                    {m.error_types.map((et) => (
                      <span
                        key={et}
                        style={{
                          padding: "1px 6px", borderRadius: "3px", fontSize: "9px",
                          background: "oklch(34% .05 25 / .1)", color: "var(--coral)",
                        }}
                      >
                        {et}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function HistoricalMatchReview({ perMatch }: { perMatch: MatchScoreDetailItem[] }) {
  if (!perMatch.length) return <div className="empty">暂无已结束比赛复盘数据</div>;

  // Group by match_id to avoid duplicates — show one row per match, expand to see all models
  const grouped = new Map<string, MatchScoreDetailItem[]>();
  for (const m of perMatch) {
    const items = grouped.get(m.match_id) ?? [];
    items.push(m);
    grouped.set(m.match_id, items);
  }
  // Sort by kickoff descending (most recent first)
  const matchGroups = [...grouped.entries()].sort(
    (a, b) => new Date(b[1][0].kickoff).getTime() - new Date(a[1][0].kickoff).getTime()
  );

  return (
    <section className="accuracy-section">
      <h3>历史比赛复盘</h3>
      <div style={{ background: "var(--paper-2)", border: "1px solid var(--line)", borderRadius: "4px" }}>
        {matchGroups.map(([matchId, models]) => (
          <MatchReviewItem key={matchId} matchId={matchId} models={models} />
        ))}
      </div>
    </section>
  );
}

function isVisibleAdaptiveSource(source: string): boolean {
  const normalized = source.toLowerCase();
  return !normalized.includes("xiaomi") && !normalized.includes("mimo");
}

// ─── Section: 影子模型观察区 ──────────────────────────────────────────
function ShadowModelObservation({ rows }: { rows: ShadowModelRow[] }) {
  if (!rows.length) return null;

  const baselineBrier = rows.find((r) => r.model_version === "elo-poisson-v1")?.brier ?? 0;

  const getRecommendationStyle = (rec: ShadowModelRow["recommendation"]) => {
    switch (rec) {
      case "consider_switch": return { color: "var(--mint)", label: "可考虑切换" };
      case "trend": return { color: "var(--amber)", label: "趋势观察" };
      case "observe": default: return { color: "var(--muted)", label: "继续观察" };
    }
  };

  return (
    <section className="accuracy-section">
      <h3>
        影子模型观察区
        <span
          style={{
            marginLeft: "8px",
            fontSize: "11px",
            fontWeight: 400,
            color: "var(--muted)",
            verticalAlign: "middle",
          }}
        >
          （不影响默认预测）
        </span>
      </h3>
      <div
        style={{
          color: "var(--muted)",
          fontSize: "12px",
          marginBottom: "10px",
          padding: "8px 12px",
          border: "1px solid var(--line)",
          borderRadius: "4px",
          background: "oklch(34% .025 260 / .06)",
        }}
      >
        影子模型与 baseline 使用相同比赛数据并行评分，仅用于观察效果，不参与默认预测生成
      </div>
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>模型</th>
              <th>样本</th>
              <th>命中率</th>
              <th>Brier</th>
              <th>LogLoss</th>
              <th>平局命中/未命中</th>
              <th>强队误判</th>
              <th>过度自信</th>
              <th>建议</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const isBaseline = r.model_version === "elo-poisson-v1";
              const isShadow = !isBaseline;
              const rec = getRecommendationStyle(r.recommendation);
              const brierDelta = baselineBrier > 0 ? r.brier - baselineBrier : 0;

              return (
                <tr
                  key={r.model_version}
                  style={{
                    background: isShadow ? "oklch(34% .015 260 / .06)" : undefined,
                  }}
                >
                  <td className="version-cell">
                    {r.label}
                    {isShadow && (
                      <span
                        style={{
                          marginLeft: "6px",
                          padding: "1px 6px",
                          borderRadius: "3px",
                          fontSize: "9px",
                          fontWeight: 600,
                          background: "oklch(34% .04 260 / .15)",
                          color: "var(--muted)",
                          textTransform: "uppercase",
                          letterSpacing: ".05em",
                        }}
                      >
                        shadow
                      </span>
                    )}
                  </td>
                  <td>{r.sample_count}</td>
                  <td>{pct(r.hit_rate)}</td>
                  <td className={r.brier <= 0.3 ? "good" : r.brier >= 0.5 ? "bad" : ""}>
                    {fmt(r.brier)}
                    {!isBaseline && brierDelta !== 0 && (
                      <span
                        style={{
                          fontSize: "10px",
                          marginLeft: "4px",
                          color: brierDelta < 0 ? "var(--mint)" : "var(--coral)",
                        }}
                      >
                        ({brierDelta > 0 ? "+" : ""}{fmt(brierDelta)})
                      </span>
                    )}
                  </td>
                  <td>{fmt(r.log_loss)}</td>
                  <td>
                    <span style={{ color: "var(--mint)" }}>{r.draw_hit}</span>
                    {" / "}
                    <span style={{ color: "var(--coral)" }}>{r.draw_miss}</span>
                  </td>
                  <td style={{ color: r.favorite_wrong > 0 ? "var(--coral)" : "var(--muted)" }}>{r.favorite_wrong}</td>
                  <td style={{ color: r.overconfident_wrong > 0 ? "var(--coral)" : "var(--muted)" }}>{r.overconfident_wrong}</td>
                  <td>
                    <span style={{ color: rec.color, fontWeight: 600, fontSize: "12px" }}>
                      {rec.label}
                    </span>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─── Section: 赛后评分对比 ──────────────────────────────────────────
function PostMatchScoringComparison({ items, insufficientSample }: { items: ModelComparisonItem[]; insufficientSample: boolean }) {
  if (!items.length) return null;

  const bestBrier = Math.min(...items.filter((i) => i.brier != null).map((i) => i.brier!));

  return (
    <section className="accuracy-section">
      <h3>赛后评分对比</h3>
      {insufficientSample && (
        <div
          style={{
            color: "var(--amber)", fontSize: "12px", marginBottom: "10px", padding: "8px 12px",
            border: "1px solid var(--amber)", borderRadius: "4px", background: "oklch(34% .025 80 / .1)",
          }}
        >
          ⚠️ 样本不足，以下对比仅供参考
        </div>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>来源</th>
              <th>模型版本</th>
              <th>Prompt</th>
              <th>角色</th>
              <th>样本</th>
              <th>Brier</th>
              <th>LogLoss</th>
              <th>命中率</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => {
              const isBest = item.brier != null && item.brier === bestBrier;
              const roleLabel = item.role === "production" ? "生产" : item.role === "shadow" ? "影子" : "未知";
              const roleColor = item.role === "production" ? "var(--mint)" : item.role === "shadow" ? "var(--amber)" : "var(--muted)";

              return (
                <tr key={item.model_version} style={{ background: item.role === "shadow" ? "oklch(34% .015 260 / .06)" : undefined }}>
                  <td style={{ fontWeight: 600 }}>{item.source}</td>
                  <td className="version-cell">{item.model_version}</td>
                  <td style={{ fontSize: "11px", color: "var(--muted)" }}>{item.prompt_version ?? "—"}</td>
                  <td>
                    <span style={{ fontSize: "11px", fontWeight: 600, color: roleColor }}>
                      {roleLabel}
                    </span>
                  </td>
                  <td>{item.available ? item.sample_count : <span style={{ color: "var(--muted)" }}>0</span>}</td>
                  <td className={item.brier != null ? (item.brier <= 0.3 ? "good" : item.brier >= 0.5 ? "bad" : "") : ""}>
                    {item.brier != null ? fmt(item.brier) : "—"}
                    {isBest && <span style={{ fontSize: "10px", marginLeft: "4px", color: "var(--mint)" }}>★</span>}
                  </td>
                  <td>{item.logloss != null ? fmt(item.logloss) : "—"}</td>
                  <td>{item.hit_rate != null ? pct(item.hit_rate) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}

// ─── Main Component ─────────────────────────────────────────────────
export default function ModelReviewCenter() {
  const cmd = useQuery({ queryKey: ["accuracy-command-center"], queryFn: getAccuracyCommandCenter });
  const aiEval = useQuery({ queryKey: ["ai-evaluation"], queryFn: getAIEvaluation });
  const modelScoreDetails = useQuery({ queryKey: ["model-score-details"], queryFn: getModelScoreDetails });
  const profileEval = useQuery({ queryKey: ["profile-evaluation"], queryFn: getProfileEvaluation });
  const matchCountBreakdownQuery = useQuery({ queryKey: ["match-count-breakdown"], queryFn: getMatchCountBreakdown });
  const matchCountBreakdownData = matchCountBreakdownQuery.data;
  const errorAttributionSummary = useQuery({ queryKey: ["error-attribution-summary"], queryFn: getErrorAttributionSummary });
  const versionScoreData = useQuery({ queryKey: ["model-score-by-version"], queryFn: getModelScoreByVersion });
  const adaptiveWeightsQuery = useQuery({ queryKey: ["adaptive-weights"], queryFn: getAdaptiveWeights, staleTime: 60_000 });

  if (cmd.isLoading) {
    return <div className="empty">加载模型复盘数据...</div>;
  }

  // Error boundary: check each query and report per-section errors
  const queryErrors: { label: string; message: string }[] = [];
  if (cmd.isError) queryErrors.push({ label: "核心数据", message: cmd.error instanceof Error ? cmd.error.message : "未知错误" });
  if (aiEval.isError) queryErrors.push({ label: "AI 评估", message: aiEval.error instanceof Error ? aiEval.error.message : "未知错误" });
  if (modelScoreDetails.isError) queryErrors.push({ label: "评分详情", message: modelScoreDetails.error instanceof Error ? modelScoreDetails.error.message : "未知错误" });
  if (profileEval.isError) queryErrors.push({ label: "画像评估", message: profileEval.error instanceof Error ? profileEval.error.message : "未知错误" });
  if (matchCountBreakdownQuery.isError) queryErrors.push({ label: "比赛统计", message: matchCountBreakdownQuery.error instanceof Error ? matchCountBreakdownQuery.error.message : "未知错误" });
  if (errorAttributionSummary.isError) queryErrors.push({ label: "错误归因", message: errorAttributionSummary.error instanceof Error ? errorAttributionSummary.error.message : "未知错误" });
  if (versionScoreData.isError) queryErrors.push({ label: "版本评分", message: versionScoreData.error instanceof Error ? versionScoreData.error.message : "未知错误" });

  if (queryErrors.length > 0) {
    return (
      <div className="empty" style={{ textAlign: "left", padding: "20px" }}>
        <div style={{ fontWeight: 700, marginBottom: 10, color: "var(--risk-red)" }}>部分数据加载失败</div>
        {queryErrors.map((e) => (
          <div key={e.label} style={{ fontSize: 12, marginBottom: 4 }}>
            <strong>{e.label}：</strong>{e.message}
          </div>
        ))}
      </div>
    );
  }

  if (!cmd.data) return <div className="empty">暂无数据</div>;

  const d = cmd.data;
  const versionScores = d.version_scores ?? [];
  const modelComparison = (d.model_comparison ?? []) as ModelComparisonItem[];
  const minSample = versionScores.length ? Math.min(...versionScores.map((v) => v.sample_count)) : 0;
  const insufficientSample = minSample < 5;
  // Use API-provided sample_count (total scored matches) instead of derived sum
  const totalSample = d.sample_count ?? (versionScores.length ? versionScores.reduce((sum, v) => sum + v.sample_count, 0) : 0);
  const baselineAvailable = d.baseline_score?.available === true;
  const baselineSampleCount = d.baseline_score?.sample_count ?? 0;

  // Scoring exclusions from accuracy command center
  const scoringExclusions = (d.scoring_exclusions ?? []) as Array<{ match_id: string; home_team: string; away_team: string; reason: string }>;

  // Per-match detail from model-score/details endpoint (correct schema)
  const perMatch = modelScoreDetails.data?.details ?? [];

  // Build shadow model rows from version score data
  const allVersionRows = versionScoreData.data?.versions ?? [];
  const baselineRow = allVersionRows.find((v) => v.model_version === "elo-poisson-v1");
  const baselineBrier = baselineRow?.brier ?? 0;

  const shadowModelRows: ShadowModelRow[] = allVersionRows.map((v) => {
    const isBaseline = v.model_version === "elo-poisson-v1";
    const brierImprovement = baselineBrier > 0 ? (baselineBrier - v.brier) / baselineBrier : 0;
    const logLossNotWorse = isBaseline || v.logloss <= (baselineRow?.logloss ?? Infinity);
    const overconfidentNotIncreased = isBaseline || v.overconfident_wrong_count <= (baselineRow?.overconfident_wrong_count ?? Infinity);

    let recommendation: ShadowModelRow["recommendation"] = "observe";
    if (v.sample_count >= 20 && brierImprovement >= 0.03 && logLossNotWorse && overconfidentNotIncreased) {
      recommendation = "consider_switch";
    } else if (v.sample_count >= 10) {
      recommendation = "trend";
    }

    return {
      model_version: v.model_version,
      label: isBaseline ? "Baseline (elo-poisson-v1)" : v.model_version.replace(/-shadow$/, ""),
      sample_count: v.sample_count,
      hit_rate: v.hit_rate,
      brier: v.brier,
      log_loss: v.logloss,
      draw_hit: (v as any).draw_hit_count ?? 0,
      draw_miss: v.draw_miss_count,
      favorite_wrong: v.favorite_overestimated_count,
      overconfident_wrong: v.overconfident_wrong_count,
      recommendation,
    };
  });

  return (
    <div className="accuracy-panel">
      {/* A. Core Conclusion Card - shown first */}
      <SectionCard title="核心结论">
        <div style={{ padding: "16px 20px", borderRadius: 6, background: insufficientSample ? "rgba(246,195,67,0.08)" : "rgba(53,217,155,0.08)", borderLeft: `4px solid ${insufficientSample ? "var(--accent-yellow)" : "var(--success-green)"}` }}>
          {matchCountBreakdownData ? (
            <div style={{ fontSize: 16, fontWeight: 700, color: insufficientSample ? "var(--accent-yellow)" : "var(--success-green)", marginBottom: 8 }}>
              已完赛 {matchCountBreakdownData.total_finished} 场，有效评分 {matchCountBreakdownData.actually_scored} 场{insufficientSample ? "。当前样本不足，不建议调整模型。" : "。"}
            </div>
          ) : (
            <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-secondary)", marginBottom: 8 }}>
              加载评分数据中...
            </div>
          )}
          <div className="metric-grid" style={{ marginTop: 12 }}>
            {matchCountBreakdownData && (
              <>
                <MetricCard label="已完赛" value={matchCountBreakdownData.total_finished} tone="neutral" />
                <MetricCard label="有赛前预测" value={matchCountBreakdownData.has_pre_match_prediction} tone="ok" />
                <MetricCard label="有开球前快照" value={matchCountBreakdownData.has_pre_kickoff_snapshot} tone={matchCountBreakdownData.has_pre_kickoff_snapshot > 0 ? "ok" : "error"} />
                <MetricCard label="实际评分" value={matchCountBreakdownData.actually_scored} tone={insufficientSample ? "warn" : "ok"} />
                <MetricCard label="未评分" value={matchCountBreakdownData.missing_snapshot} tone={matchCountBreakdownData.missing_snapshot > 0 ? "error" : "neutral"} />
              </>
            )}
            <MetricCard label="样本数" value={totalSample} tone={insufficientSample ? "warn" : "ok"} note={insufficientSample ? "不足（需≥5）" : "充分"} />
            <MetricCard label="默认模型" value={d.model_recommendation?.recommended_model_version ?? "baseline"} tone={insufficientSample ? "neutral" : "ok"} />
          </div>
        </div>
      </SectionCard>

      {/* B. 自适应 Ensemble 权重 (BMA v2) */}
      <SectionCard title="自适应 Ensemble 权重" badge={adaptiveWeightsQuery.data?.is_adaptive ? "BMA 已启用" : "BMA 待激活"}>
        {adaptiveWeightsQuery.data ? (() => {
          const aw = adaptiveWeightsQuery.data;
          const visiblePerformanceEntries = Object.entries(aw.performance ?? {}).filter(([src]) => isVisibleAdaptiveSource(src));
          const visibleWeightEntries = Object.entries(aw.weights ?? {}).filter(([src]) => isVisibleAdaptiveSource(src));
          const sysW = aw.weights.system ?? 0;
          const mktW = aw.weights.market ?? 0;
          const aiTotal = visibleWeightEntries.filter(([k]) => k.startsWith("ai_")).reduce((s, [, v]) => s + v, 0);
          const sysPerf = aw.performance.system;
          const mktPerf = aw.performance.market;
          const minSample = aw.config.min_sample_size;
          const sysReady = (sysPerf?.sample_count ?? 0) >= minSample;
          const mktReady = (mktPerf?.sample_count ?? 0) >= minSample;
          const sigPairs = Object.entries(aw.significance ?? {}).filter(([, v]: any) => v.significant);
          const isBMA = aw.config?.algorithm === "bayesian_model_averaging_v2";
          return (
            <div>
              <div style={{ fontSize: 11, color: aw.is_adaptive ? "var(--success-green)" : "var(--accent-yellow)", marginBottom: 10, padding: "6px 10px", border: `1px solid ${aw.is_adaptive ? "var(--success-green)" : "var(--accent-yellow)"}`, borderRadius: 3, background: aw.is_adaptive ? "rgba(53,217,155,0.08)" : "rgba(246,195,67,0.08)" }}>
                {aw.is_adaptive
                  ? `贝叶斯模型平均已激活：基于后验分布 + 配对显著性检验动态调整权重（半衰期 ${aw.config.time_decay_half_life} 场）`
                  : `样本不足（需 ≥${minSample} 场，当前系统 ${sysPerf?.sample_count ?? 0} 场、市场 ${mktPerf?.sample_count ?? 0} 场），使用默认权重`}
              </div>
              <div className="metric-grid">
                <MetricCard label="系统权重" value={`${(sysW * 100).toFixed(1)}%`} tone={sysReady ? "ok" : "warn"} note={sysPerf && (sysPerf.posterior_mu ?? sysPerf.brier) != null ? `后验 Brier ${fmt((sysPerf.posterior_mu ?? sysPerf.brier)! )}` : ""} />
                <MetricCard label="市场权重" value={`${(mktW * 100).toFixed(1)}%`} tone={mktReady ? "ok" : "warn"} note={mktPerf && (mktPerf.posterior_mu ?? mktPerf.brier) != null ? `后验 Brier ${fmt((mktPerf.posterior_mu ?? mktPerf.brier)! )}` : ""} />
                <MetricCard label="AI 总权重" value={`${(aiTotal * 100).toFixed(1)}%`} tone="neutral" />
                <MetricCard label="显著差异对" value={`${sigPairs.length}`} tone={sigPairs.length > 0 ? "ok" : "neutral"} note={sigPairs.length > 0 ? "有统计支撑" : "尚无显著差异"} />
              </div>
              {/* Bayesian credibility intervals */}
              {isBMA && sysPerf && mktPerf && (
                <div style={{ marginTop: 10, fontSize: 11 }}>
                  <div style={{ color: "var(--muted)", marginBottom: 4 }}>贝叶斯后验 95% 可信区间</div>
                  <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                    {visiblePerformanceEntries.map(([src, perf]: [string, any]) => {
                      if (!perf.ci_95) return null;
                      const label = src === "system" ? "系统" : src === "market" ? "市场" : src.replace("ai_ai-", "").replace(/-v\d+$/, "");
                      return (
                        <div key={src} style={{ padding: "4px 10px", borderRadius: 3, background: "var(--paper-2)", fontSize: 11 }}>
                          <span style={{ color: "var(--text-secondary)" }}>{label}</span>{" "}
                          <strong>[{fmt(perf.ci_95[0])}, {fmt(perf.ci_95[1])}]</strong>
                          <span style={{ color: "var(--muted)", marginLeft: 4 }}>n={perf.effective_n ?? perf.sample_count}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {/* Significance test results */}
              {sigPairs.length > 0 && (
                <div style={{ marginTop: 10, fontSize: 11 }}>
                  <div style={{ color: "var(--amber)", marginBottom: 4 }}>显著差异（p &lt; {aw.config.significance_level}）</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {sigPairs.map(([pairKey, result]: [string, any]) => {
                      const better = (result.better_source ?? "").replace("ai_ai-", "").replace(/-v\d+$/, "");
                      return (
                        <div key={pairKey} style={{ padding: "4px 10px", borderRadius: 3, background: "rgba(53,217,155,0.08)", border: "1px solid var(--success-green)", fontSize: 11 }}>
                          <strong style={{ color: "var(--success-green)" }}>{better}</strong> 显著更优
                          <span style={{ color: "var(--muted)", marginLeft: 4 }}>p={fmt(result.p_value)} t={fmt(result.t_stat)}</span>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              {/* Per-AI-model weights */}
              {visibleWeightEntries.filter(([k]) => k.startsWith("ai_")).length > 0 && (
                <div style={{ marginTop: 10 }}>
                  <div style={{ fontSize: 11, color: "var(--muted)", marginBottom: 4 }}>AI 模型权重分布</div>
                  <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                    {visibleWeightEntries.filter(([k]) => k.startsWith("ai_")).map(([k, v]) => {
                      const label = k.replace("ai_ai-", "").replace(/-v\d+$/, "");
                      const perf = aw.performance[k];
                      return (
                        <div key={k} style={{ padding: "4px 10px", borderRadius: 3, background: "var(--paper-2)", fontSize: 11 }}>
                          <span style={{ color: "var(--text-secondary)" }}>{label}</span>{" "}
                          <strong>{(v * 100).toFixed(1)}%</strong>
                          {perf?.posterior_mu != null && <span style={{ color: "var(--muted)", marginLeft: 4 }}>B:{fmt(perf.posterior_mu)}</span>}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
              <div style={{ fontSize: 10, color: "var(--muted)", marginTop: 8 }}>
                算法：{isBMA ? "贝叶斯模型平均 v2" : "v1"} | 上次更新：{aw.last_updated ? formatChinaTimeShort(aw.last_updated) : "—"}
              </div>
            </div>
          );
        })() : <EmptyState>加载自适应权重数据...</EmptyState>}
      </SectionCard>

      {/* C. Baseline表现 */}
      <SectionCard title="Baseline 表现">
        {baselineAvailable ? (
          <div style={{ display: "flex", gap: "10px", marginBottom: "12px", flexWrap: "wrap" }}>
            <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
              <span style={{ color: "var(--muted)" }}>样本: </span>
              <strong style={{ color: baselineSampleCount > 0 ? "var(--mint)" : "var(--coral)" }}>{baselineSampleCount}</strong>
            </div>
            {d.baseline_score?.brier != null && (
              <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
                <span style={{ color: "var(--muted)" }}>Brier: </span>
                <strong>{fmt(d.baseline_score.brier)}</strong>
              </div>
            )}
            {d.baseline_score?.hit_rate != null && (
              <div style={{ padding: "6px 14px", borderRadius: "4px", background: "var(--paper-2)", fontSize: "13px" }}>
                <span style={{ color: "var(--muted)" }}>命中率: </span>
                <strong>{pct(d.baseline_score.hit_rate)}</strong>
              </div>
            )}
          </div>
        ) : (
          <div style={{ color: "var(--muted)", fontSize: "12px", marginBottom: "10px", padding: "8px 12px", border: "1px solid var(--line)", borderRadius: "4px" }}>
            Baseline 暂无数据
          </div>
        )}
        <ModelComparison versions={versionScores} insufficientSample={insufficientSample} />
      </SectionCard>

      {/* C. Team Profile表现 */}
      <SectionCard title="球队画像模型表现">
        {profileEval.data ? <>
          <div style={{ fontSize: "11px", color: "var(--amber)", marginBottom: 8, padding: "6px 10px", border: "1px solid var(--amber)", borderRadius: 3, background: "oklch(34% .025 80 / .1)" }}>
            数据来源：已完赛国际比赛结果快照；画像模型仍作为独立影子候选评估。
          </div>
          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginBottom: 6 }}>模型版本: {profileEval.data.model_version}</div>
          <div className="profile-review-metrics">
            <div><span>样本</span><strong>{profileEval.data.sample_count}</strong></div>
            <div><span>Baseline Brier</span><strong>{profileEval.data.baseline_brier?.toFixed(4) ?? "—"}</strong></div>
            <div><span>Profile Brier</span><strong>{profileEval.data.profile_brier?.toFixed(4) ?? "—"}</strong></div>
            <div className="good"><span>Helped</span><strong>{profileEval.data.helped}</strong></div>
            <div className="bad"><span>Hurt</span><strong>{profileEval.data.hurt}</strong></div>
            <div><span>Neutral</span><strong>{profileEval.data.neutral}</strong></div>
          </div>
          <div className="profile-trait-review"><div><strong>最有用 trait</strong><p>{(profileEval.data.most_helpful_traits || []).map((x: {trait: string; count: number}) => `${x.trait} ×${x.count}`).join("、") || "样本不足"}</p></div><div><strong>最容易误导 trait</strong><p>{(profileEval.data.most_misleading_traits || []).map((x: {trait: string; count: number}) => `${x.trait} ×${x.count}`).join("、") || "暂无"}</p></div></div>
        </> : <EmptyState>画像模型评分加载中...</EmptyState>}
      </SectionCard>

      {/* D. AI表现 */}
      <SectionCard title="AI 表现">
        <AIComparison aiEvaluation={aiEval.data ?? null} insufficientSample={insufficientSample} />
      </SectionCard>

      {/* D+. 赛后评分对比 */}
      <SectionCard title="赛后评分对比" badge="Baseline vs AI v1 vs AI v2 vs Ensemble">
        <PostMatchScoringComparison items={modelComparison} insufficientSample={insufficientSample} />
      </SectionCard>

      {/* E. 影子模型观察 */}
      <SectionCard title="影子模型观察" badge="不影响默认预测">
        <ShadowModelObservation rows={shadowModelRows} />
      </SectionCard>

      {/* E+. 错误归因 */}
      <ErrorAttribution versions={versionScores} errorSummary={errorAttributionSummary.data ?? null} />

      {/* F. 未评分比赛 */}
      <ScoringExclusions exclusions={scoringExclusions} />

      {/* G. 历史比赛复盘 */}
      <SectionCard title="历史比赛复盘">
        <HistoricalMatchReview perMatch={perMatch} />
      </SectionCard>
    </div>
  );
}
