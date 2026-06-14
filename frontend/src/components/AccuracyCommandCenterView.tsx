import { useQuery } from "@tanstack/react-query";
import { getAccuracyCommandCenter } from "../api";
import { formatChinaTimeShort } from "../utils/time";

const STATUS_CLASS: Record<string, string> = {
  ready: "good",
  disabled: "warn",
  disabled_no_key: "bad",
  error: "bad",
  unconfigured: "bad",
};
const STATUS_LABELS: Record<string, string> = {
  ready: "就绪",
  disabled: "已禁用",
  disabled_no_key: "未配置密钥",
  error: "错误",
  unconfigured: "未配置",
};
const STATUS_ICON: Record<string, string> = {
  ready: "🟢",
  disabled: "🟡",
  disabled_no_key: "🔴",
  error: "🔴",
  unconfigured: "⚪",
};

function fmt(n: number, digits = 4) { return n.toFixed(digits); }
function pct(n: number) { return (n * 100).toFixed(1) + "%"; }

function SummaryBlock({
  title,
  value,
  note,
  tone = "neutral",
}: {
  title: string;
  value: string;
  note: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  return (
    <div className={`summary-block ${tone}`}>
      <div className="summary-block-title">{title}</div>
      <div className="summary-block-value">{value}</div>
      <div className="summary-block-note">{note}</div>
    </div>
  );
}

export default function AccuracyCommandCenterView() {
  const cmd = useQuery({ queryKey: ["accuracy-command-center"], queryFn: getAccuracyCommandCenter });

  if (cmd.isLoading) return <div className="app-empty-state">加载准确率指挥室数据...</div>;
  if (cmd.isError) return <div className="app-empty-state error">准确率指挥室数据加载失败: {cmd.error instanceof Error ? cmd.error.message : "未知错误"}</div>;
  if (!cmd.data) return <div className="app-empty-state">暂无数据</div>;

  const d = cmd.data;
  const minSample = d.version_scores?.length ? Math.min(...d.version_scores.map(v => v.sample_count)) : 0;
  const insufficientSample = minSample < 5;

  // Find the best model by Brier score
  const bestByVersion = d.version_scores?.length
    ? d.version_scores.reduce((best, v) => v.brier < best.brier ? v : best, d.version_scores[0])
    : null;

  // Determine error patterns from version scores
  const latestVersion = d.version_scores?.[0];
  const errorPatterns: string[] = [];
  if (latestVersion) {
    if (latestVersion.draw_miss_count > 0) errorPatterns.push(`平局低估 (${latestVersion.draw_miss_count} 场)`);
    if (latestVersion.favorite_overestimated_count > 0) errorPatterns.push(`强队高估 (${latestVersion.favorite_overestimated_count} 场)`);
    if (latestVersion.underdog_underestimated_count > 0) errorPatterns.push(`弱队低估 (${latestVersion.underdog_underestimated_count} 场)`);
    if (latestVersion.overconfident_wrong_count > 0) errorPatterns.push(`过度自信 (${latestVersion.overconfident_wrong_count} 场)`);
  }

  // Determine if AI/ensemble is helping
  const aiHelping = d.ai_evaluation?.ensemble
    ? d.ai_evaluation.ensemble.helped > d.ai_evaluation.ensemble.hurt
    : false;
  const aiNeutral = d.ai_evaluation?.ensemble
    ? d.ai_evaluation.ensemble.helped === d.ai_evaluation.ensemble.hurt
    : true;

  // Next recommended version
  const nextVersion = d.version_scores?.length && d.version_scores.length > 1
    ? d.version_scores[1]?.model_version
    : null;

  // Why we can't conclude yet
  const cantConcludeReasons: string[] = [];
  if (insufficientSample) cantConcludeReasons.push(`样本量不足 (最少 ${minSample} 场，需 ≥5 场)`);
  if (d.ai_evaluation?.system?.sample_count && d.ai_evaluation.system.sample_count < 5) cantConcludeReasons.push("AI 评估系统样本不足");
  if (d.market_comparison?.market_sample_count && d.market_comparison.market_sample_count < 5) cantConcludeReasons.push("市场对比样本不足");

  return (
    <div className="accuracy-panel command-center-view">
      <h2 style={{ textTransform: "uppercase", letterSpacing: ".1em", fontSize: "14px", color: "var(--amber)", marginBottom: "20px" }}>
        准确率指挥室
      </h2>

      <section className="accuracy-section">
        <h3>关键摘要</h3>
        <div className="summary-grid-panels">
          <SummaryBlock
            title="推荐模型"
            value={d.model_recommendation ? d.model_recommendation.recommended_model_version : "暂无"}
            note={d.model_recommendation ? d.model_recommendation.confidence : "暂无推荐"}
            tone={insufficientSample ? "warn" : "good"}
          />
          <SummaryBlock
            title="样本充分性"
            value={insufficientSample ? "不足" : "充分"}
            note={insufficientSample ? `最少 ${minSample} 场，需 ≥5` : `最少 ${minSample} 场，可结论`}
            tone={insufficientSample ? "warn" : "good"}
          />
          <SummaryBlock
            title="AI / Ensemble"
            value={d.ai_evaluation?.ensemble ? (aiHelping ? "正向" : aiNeutral ? "中性" : "负向") : "暂无"}
            note={d.ai_evaluation?.ensemble ? `帮助 ${d.ai_evaluation.ensemble.helped} / 损害 ${d.ai_evaluation.ensemble.hurt}` : "暂无评估数据"}
            tone={d.ai_evaluation?.ensemble ? (aiHelping ? "good" : aiNeutral ? "warn" : "bad") : "neutral"}
          />
          <SummaryBlock
            title="市场对比"
            value={d.market_comparison?.market_sample_count ? `${d.market_comparison.market_sample_count} 场` : "暂无"}
            note={d.market_comparison?.market_sample_count ? `建议权重 ${d.market_comparison.suggested_market_blend_weight}` : "暂无市场数据"}
            tone={d.market_comparison?.market_sample_count ? "good" : "neutral"}
          />
        </div>
      </section>

      {/* Recommendation — most prominent */}
      <section className="accuracy-section">
        <h3>推荐模型</h3>
        {d.model_recommendation ? (
          <div className="recommendation-card" style={{ borderColor: insufficientSample ? "var(--amber)" : "var(--mint)", borderWidth: "2px" }}>
            {insufficientSample ? (
              <>
                <div className="rec-version" style={{ fontSize: "18px", color: "var(--amber)" }}>
                  当前默认推荐: <strong>基线模型</strong>
                </div>
                <div className="rec-reason" style={{ color: "var(--amber)" }}>
                  样本不足，暂不能下结论。在样本量足够之前，建议使用基线模型作为默认选择。
                </div>
                <div className="rec-meta" style={{ opacity: 0.7 }}>
                  <span>系统推荐: {d.model_recommendation.recommended_model_version}（样本充足后生效）</span>
                  <span>置信度: {d.model_recommendation.confidence}</span>
                  {d.model_recommendation.sample_warning && <span className="warn">⚠️ {d.model_recommendation.sample_warning}</span>}
                  <span>备用: {d.model_recommendation.fallback_model_version}</span>
                </div>
              </>
            ) : (
              <>
                <div className="rec-version" style={{ fontSize: "18px" }}>
                  推荐模型: <strong style={{ color: "var(--mint)" }}>{d.model_recommendation.recommended_model_version}</strong>
                </div>
                <div className="rec-reason">{d.model_recommendation.reason}</div>
                <div className="rec-meta">
                  <span>置信度: {d.model_recommendation.confidence}</span>
                  {d.model_recommendation.sample_warning && <span className="warn">⚠️ {d.model_recommendation.sample_warning}</span>}
                  <span>备用: {d.model_recommendation.fallback_model_version}</span>
                  {d.model_recommendation.brier_improvement != null && (
                    <span>Brier改善: {fmt(d.model_recommendation.brier_improvement)}</span>
                  )}
                  {d.model_recommendation.relative_improvement != null && (
                    <span>相对改善: {pct(d.model_recommendation.relative_improvement)}</span>
                  )}
                </div>
              </>
            )}
          </div>
        ) : <div className="app-empty-state">暂无推荐</div>}
      </section>

      {/* Sample sufficiency status */}
      <section className="accuracy-section">
        <h3>样本充分性</h3>
        <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
          <div className="metric" style={{ padding: "8px 12px", borderRadius: "4px", background: insufficientSample ? "oklch(34% .05 25 / .1)" : "oklch(34% .05 150 / .1)", borderLeft: `3px solid ${insufficientSample ? "var(--coral)" : "var(--mint)"}` }}>
            <div className="metric-label">模型版本最少样本</div>
            <div className={`metric-value ${insufficientSample ? "bad" : "good"}`}>{minSample} 场</div>
            <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>{insufficientSample ? "不足 (需 ≥5)" : "充分"}</div>
          </div>
          {d.ai_evaluation?.system && (
            <div className="metric" style={{ padding: "8px 12px", borderRadius: "4px", background: d.ai_evaluation.system.sample_count < 5 ? "oklch(34% .05 25 / .1)" : "oklch(34% .05 150 / .1)", borderLeft: `3px solid ${d.ai_evaluation.system.sample_count < 5 ? "var(--coral)" : "var(--mint)"}` }}>
              <div className="metric-label">AI 评估样本</div>
              <div className={`metric-value ${d.ai_evaluation.system.sample_count < 5 ? "bad" : "good"}`}>{d.ai_evaluation.system.sample_count} 场</div>
              <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>{d.ai_evaluation.system.sample_count < 5 ? "不足" : "充分"}</div>
            </div>
          )}
          {d.market_comparison && (
            <div className="metric" style={{ padding: "8px 12px", borderRadius: "4px", background: d.market_comparison.market_sample_count < 5 ? "oklch(34% .05 25 / .1)" : "oklch(34% .05 150 / .1)", borderLeft: `3px solid ${d.market_comparison.market_sample_count < 5 ? "var(--coral)" : "var(--mint)"}` }}>
              <div className="metric-label">市场对比样本</div>
              <div className={`metric-value ${d.market_comparison.market_sample_count < 5 ? "bad" : "good"}`}>{d.market_comparison.market_sample_count} 场</div>
              <div style={{ fontSize: "11px", color: "var(--text-dim)" }}>{d.market_comparison.market_sample_count < 5 ? "不足" : "充分"}</div>
            </div>
          )}
        </div>
      </section>

      {/* Currently best model */}
      <section className="accuracy-section">
        <h3>当前最佳模型</h3>
        {bestByVersion ? (
          <div style={{ padding: "8px 12px", borderRadius: "4px", background: "oklch(34% .05 150 / .1)", borderLeft: "3px solid var(--mint)" }}>
            <div style={{ fontWeight: 600, fontSize: "14px" }}>{bestByVersion.model_version}</div>
            <div style={{ display: "flex", gap: "16px", fontSize: "12px", marginTop: "4px" }}>
              <span>Brier: {fmt(bestByVersion.brier)}</span>
              <span>LogLoss: {fmt(bestByVersion.logloss)}</span>
              <span>命中率: {pct(bestByVersion.hit_rate)}</span>
              <span>样本: {bestByVersion.sample_count} 场</span>
            </div>
            {insufficientSample && (
              <div style={{ fontSize: "11px", color: "var(--amber)", marginTop: "4px" }}>
                ⚠️ 样本不足，此排名可能不稳定
              </div>
            )}
          </div>
        ) : <div className="app-empty-state">暂无版本评分数据</div>}
      </section>

      {/* Error patterns */}
      <section className="accuracy-section">
        <h3>错误模式</h3>
        {errorPatterns.length > 0 ? (
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            {errorPatterns.map((pattern) => (
              <span key={pattern} style={{ padding: "4px 10px", borderRadius: "3px", background: "oklch(34% .05 25 / .1)", borderLeft: "3px solid var(--coral)", fontSize: "12px" }}>
                {pattern}
              </span>
            ))}
          </div>
        ) : (
          <div style={{ fontSize: "12px", color: "var(--mint)" }}>当前无明显错误模式</div>
        )}
      </section>

      {/* AI / Ensemble helping status */}
      <section className="accuracy-section">
        <h3>AI / Ensemble 效果</h3>
        {d.ai_evaluation?.ensemble ? (
          <div style={{ padding: "8px 12px", borderRadius: "4px", background: aiHelping ? "oklch(34% .05 150 / .1)" : aiNeutral ? "oklch(34% .025 80 / .1)" : "oklch(34% .05 25 / .1)", borderLeft: `3px solid ${aiHelping ? "var(--mint)" : aiNeutral ? "var(--amber)" : "var(--coral)"}` }}>
            <div style={{ fontWeight: 600, fontSize: "14px" }}>
              {aiHelping ? "✅ AI/Ensemble 正在帮助提升预测" : aiNeutral ? "➖ AI/Ensemble 效果中性" : "❌ AI/Ensemble 当前有损害"}
            </div>
            <div style={{ display: "flex", gap: "16px", fontSize: "12px", marginTop: "4px" }}>
              <span>帮助: {d.ai_evaluation.ensemble.helped} 场</span>
              <span>损害: {d.ai_evaluation.ensemble.hurt} 场</span>
              <span>Brier: {d.ai_evaluation.ensemble.brier?.toFixed(4) ?? "-"}</span>
              <span>命中率: {d.ai_evaluation.ensemble.hit_rate != null ? pct(d.ai_evaluation.ensemble.hit_rate) : "-"}</span>
            </div>
            {d.ai_evaluation.system && (
              <div style={{ fontSize: "11px", color: "var(--text-dim)", marginTop: "4px" }}>
                对比系统基线: Brier {d.ai_evaluation.system.brier?.toFixed(4) ?? "-"} / 命中率 {d.ai_evaluation.system.hit_rate != null ? pct(d.ai_evaluation.system.hit_rate) : "-"}
              </div>
            )}
            {insufficientSample && (
              <div style={{ fontSize: "11px", color: "var(--amber)", marginTop: "4px" }}>
                ⚠️ 样本不足，此结论可能不可靠
              </div>
            )}
          </div>
        ) : (
          <div className="app-empty-state">暂无 AI 评估数据</div>
        )}
      </section>

      {/* Next recommended version */}
      <section className="accuracy-section">
        <h3>下一推荐版本</h3>
        {nextVersion ? (
          <div style={{ padding: "6px 10px", borderRadius: "3px", background: "oklch(34% .025 250 / .1)", fontSize: "12px" }}>
            <span>可尝试: <strong>{nextVersion}</strong></span>
            <span style={{ marginLeft: "8px", color: "var(--text-dim)" }}>（当前推荐之外的备选版本）</span>
          </div>
        ) : (
          <div style={{ fontSize: "12px", color: "var(--text-dim)" }}>暂无其他版本可推荐</div>
        )}
      </section>

      {/* Why we can't conclude */}
      {cantConcludeReasons.length > 0 && (
        <section className="accuracy-section">
          <h3>无法结论原因</h3>
          <div style={{ padding: "8px 12px", borderRadius: "4px", background: "oklch(34% .05 80 / .1)", borderLeft: "3px solid var(--amber)" }}>
            {cantConcludeReasons.map((reason) => (
              <div key={reason} style={{ fontSize: "12px", marginBottom: "2px" }}>⚠️ {reason}</div>
            ))}
            <div style={{ fontSize: "11px", color: "var(--text-dim)", marginTop: "4px" }}>
              在样本量充足之前，所有评分和推荐仅供参考，默认使用基线模型。
            </div>
          </div>
        </section>
      )}

      {/* AI Model Status */}
      <section className="accuracy-section">
        <h3>AI 模型状态</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>模型</th><th>提供商</th><th>状态</th><th>角色</th><th>提供商健康</th><th>最近成功</th>
              </tr>
            </thead>
            <tbody>
              {(d.ai_models?.models || []).map((m) => (
                <tr key={m.model_version}>
                  <td className="version-cell">{m.display_name}</td>
                  <td>{m.provider}</td>
                  <td className={STATUS_CLASS[m.status] || ""}>
                    {STATUS_ICON[m.status] || ""} {STATUS_LABELS[m.status] || m.status}
                    {m.status === "disabled_no_key" && (
                      <span style={{ display: "block", fontSize: "10px", color: "var(--coral)" }}>需要配置 API 密钥</span>
                    )}
                  </td>
                  <td>{m.role}</td>
                  <td>
                    {m.provider_health ? (
                      <span style={{ color: m.provider_health.available ? "var(--mint)" : "var(--coral)" }}>
                        {m.provider_health.available ? "可用" : "不可用"}
                      </span>
                    ) : "-"}
                  </td>
                  <td>{m.last_success_at ? formatChinaTimeShort(m.last_success_at) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!d.ai_models?.enabled && (
          <div style={{ color: "var(--amber)", fontSize: "12px", marginTop: "8px" }}>
            AI 预测当前未启用。设置 ENABLE_AI_PREDICTION=true 开启。
          </div>
        )}
      </section>

      {/* Version scores */}
      <section className="accuracy-section">
        <h3>模型版本评分</h3>
        {d.version_scores?.length ? (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr><th>模型版本</th><th>样本</th><th>命中率</th><th>Brier</th><th>LogLoss</th><th>强队高估</th><th>平局漏判</th><th>弱队低估</th><th>过度自信错</th></tr>
                </thead>
                <tbody>
                  {d.version_scores.map((v) => (
                    <tr key={v.model_version}>
                      <td className="version-cell">{v.model_version}</td>
                      <td>{v.sample_count}</td>
                      <td>{pct(v.hit_rate)}</td>
                      <td className={v.brier <= 0.3 ? "good" : v.brier >= 0.5 ? "bad" : ""}>{fmt(v.brier)}</td>
                      <td>{fmt(v.logloss)}</td>
                      <td className={v.favorite_overestimated_count > 0 ? "bad" : ""}>{v.favorite_overestimated_count}</td>
                      <td className={v.draw_miss_count > 0 ? "warn" : ""}>{v.draw_miss_count}</td>
                      <td className={v.underdog_underestimated_count > 0 ? "warn" : ""}>{v.underdog_underestimated_count}</td>
                      <td className={v.overconfident_wrong_count > 0 ? "bad" : ""}>{v.overconfident_wrong_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {insufficientSample && (
              <div className="sample-warning" style={{ color: "var(--coral)", fontSize: "13px", marginTop: "8px", padding: "8px 12px", border: "2px solid var(--coral)", borderRadius: "4px", background: "oklch(34% .05 25 / .1)", fontWeight: 600 }}>
                ⚠️ 样本不足，暂不能下结论。当前最少仅 {minSample} 场样本，建议至少5场已结束比赛后再做判断。
              </div>
            )}
          </>
        ) : <div className="app-empty-state">暂无版本评分</div>}
      </section>

      {/* AI Evaluation */}
      <section className="accuracy-section">
        <h3>AI 评分效果</h3>
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>模型</th><th>样本</th><th>Brier</th><th>LogLoss</th><th>命中率</th><th>帮助</th><th>损害</th><th>效果</th></tr>
            </thead>
            <tbody>
              {d.ai_evaluation?.system && (
                <tr>
                  <td className="version-cell">系统基线 (Elo+Poisson)</td>
                  <td>{d.ai_evaluation.system.sample_count}</td>
                  <td>{d.ai_evaluation.system.brier?.toFixed(4) || "-"}</td>
                  <td>{d.ai_evaluation.system.logloss?.toFixed(4) || "-"}</td>
                  <td>{d.ai_evaluation.system.hit_rate != null ? pct(d.ai_evaluation.system.hit_rate) : "-"}</td>
                  <td>-</td><td>-</td><td>基线</td>
                </tr>
              )}
              {Object.entries(d.ai_evaluation?.ai_by_version || {}).map(([version, data]: [string, any]) => {
                const effect = d.ai_evaluation?.ai_effect?.[version];
                return (
                  <tr key={version}>
                    <td className="version-cell">{version}</td>
                    <td>{data.sample_count}</td>
                    <td>{data.brier?.toFixed(4) || "-"}</td>
                    <td>{data.logloss?.toFixed(4) || "-"}</td>
                    <td>{data.hit_rate != null ? pct(data.hit_rate) : "-"}</td>
                    <td className="good">{data.helped}</td>
                    <td className="bad">{data.hurt}</td>
                    <td className={effect?.effect === "helped" ? "good" : effect?.effect === "hurt" ? "bad" : ""}>
                      {effect?.effect === "helped" ? "有帮助" : effect?.effect === "hurt" ? "有损害" : "中性"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      {/* Market comparison */}
      <section className="accuracy-section">
        <h3>市场赔率对比</h3>
        {d.market_comparison && d.market_comparison.market_sample_count > 0 ? (
          <div className="market-comparison">
            <div className="comparison-metrics">
              <div className="metric">
                <div className="metric-label">模型 Brier</div>
                <div className={`metric-value ${d.market_comparison.model_brier <= d.market_comparison.market_brier ? "good" : "bad"}`}>
                  {fmt(d.market_comparison.model_brier)}
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">市场 Brier</div>
                <div className={`metric-value ${d.market_comparison.market_brier <= d.market_comparison.model_brier ? "good" : "bad"}`}>
                  {fmt(d.market_comparison.market_brier)}
                </div>
              </div>
              <div className="metric">
                <div className="metric-label">混合 Brier</div>
                <div className="metric-value">{fmt(d.market_comparison.blended_brier)}</div>
              </div>
            </div>
          </div>
        ) : <div className="app-empty-state">暂无市场赔率数据</div>}
      </section>

      {/* Calibration */}
      <section className="accuracy-section">
        <h3>概率校准</h3>
        {d.calibration?.buckets?.length ? (
          <div className="table-wrap">
            <table className="data-table calibration-table">
              <thead><tr><th>概率区间</th><th>样本数</th><th>预测平均</th><th>实际命中率</th><th>校准偏差</th><th>备注</th></tr></thead>
              <tbody>
                {d.calibration.buckets.map((b) => (
                  <tr key={b.label}>
                    <td>{b.label}</td>
                    <td>{b.sample_count}</td>
                    <td>{pct(b.predicted_avg_prob)}</td>
                    <td>{pct(b.actual_win_rate)}</td>
                    <td className={Math.abs(b.calibration_gap) < 0.05 ? "good" : Math.abs(b.calibration_gap) > 0.15 ? "bad" : "warn"}>
                      {b.calibration_gap >= 0 ? "+" : ""}{pct(b.calibration_gap)}
                    </td>
                    <td className="note">{b.note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : <div className="app-empty-state">暂无校准数据</div>}
      </section>

      {/* Data quality */}
      <section className="accuracy-section">
        <h3>数据质量</h3>
        {d.data_quality ? (
          <div className="quality-checks">
            <div className="quality-summary">
              总体: {d.data_quality.summary.overall_status === "pass" ? "✅" : d.data_quality.summary.overall_status === "warn" ? "⚠️" : "❌"}
              {" "}{d.data_quality.summary.pass}通过 / {d.data_quality.summary.warn}警告 / {d.data_quality.summary.fail}失败
            </div>
            {d.data_quality.checks.map((c) => (
              <div key={c.check} className={`quality-item ${c.status}`}>
                <span className="quality-icon">{c.status === "pass" ? "✅" : c.status === "warn" ? "⚠️" : "❌"}</span>
                <span className="quality-name">{c.check}</span>
                <span className="quality-count">({c.count})</span>
                {c.note && <span className="quality-note">{c.note}</span>}
              </div>
            ))}
          </div>
        ) : <div className="app-empty-state">暂无数据质量报告</div>}
      </section>
    </div>
  );
}
