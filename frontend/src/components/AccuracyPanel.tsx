import { useQuery } from "@tanstack/react-query";
import {
  getModelScoreByVersion, getCalibration,
  getMarketComparison, getModelRecommendation, getDataQuality, getAIEvaluation,
} from "../api";
import type {
  VersionScoreSummary, CalibrationBucket,
  MarketComparisonData, ModelRecommendation, DataQualityReport,
} from "../types";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="accuracy-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function fmt(n: number, digits = 4) {
  return n.toFixed(digits);
}
function pct(n: number) {
  return (n * 100).toFixed(1) + "%";
}

function QueryWrapper({ isLoading, isError, error, children }: {
  isLoading: boolean; isError: boolean; error: Error | null; children: React.ReactNode;
}) {
  if (isLoading) return <div className="app-empty-state">加载中...</div>;
  if (isError) return <div className="app-empty-state error">数据加载失败: {error?.message || "未知错误"}</div>;
  return <>{children}</>;
}

function SummaryBlock({
  title,
  value,
  note,
  tone = "neutral",
}: {
  title: string;
  value: string;
  note?: string;
  tone?: "neutral" | "good" | "warn" | "bad";
}) {
  return (
    <div className={`summary-block ${tone}`}>
      <div className="summary-block-title">{title}</div>
      <div className="summary-block-value">{value}</div>
      {note && <div className="summary-block-note">{note}</div>}
    </div>
  );
}

// 1. Model version comparison table
function VersionTable({ versions }: { versions: VersionScoreSummary[] }) {
  if (!versions.length) return <p className="app-empty-state">暂无评分数据</p>;
  const minSample = Math.min(...versions.map(v => v.sample_count));
  const insufficient = minSample < 5;
  return (
    <>
      {insufficient && (
        <div className="sample-warning" style={{ color: "var(--coral)", fontSize: "13px", marginBottom: "8px", padding: "8px 12px", border: "2px solid var(--coral)", borderRadius: "4px", background: "oklch(34% .05 25 / .1)", fontWeight: 600 }}>
          ⚠️ 样本不足，暂不能下结论。当前最少仅 {minSample} 场样本，建议至少5场已结束比赛后再做判断。当前默认推荐使用 baseline 模型。
        </div>
      )}
      <div className="table-wrap">
        <table className="data-table">
          <thead>
            <tr>
              <th>模型版本</th><th>样本</th><th>命中率</th><th>Brier</th><th>LogLoss</th>
              <th>强队高估</th><th>平局漏判</th><th>弱队低估</th><th>过度自信错</th>
              <th>警告有效</th><th>修正有效</th>
            </tr>
          </thead>
          <tbody>
            {versions.map((v) => (
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
                <td className="good">{v.warning_helped_count}</td>
                <td className="good">{v.numerical_helped_count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

// 2. Error attribution
function ErrorAttribution({ versions }: { versions: VersionScoreSummary[] }) {
  if (!versions.length) return <p className="app-empty-state">暂无数据</p>;
  const latest = versions[0];
  return (
    <div className="error-cards">
      <div className="error-card">
        <div className="error-label">强队高估</div>
        <div className="error-value">{latest.favorite_overestimated_count}</div>
        <div className="error-desc">模型对强队概率过高</div>
      </div>
      <div className="error-card">
        <div className="error-label">平局漏判</div>
        <div className="error-value">{latest.draw_miss_count}</div>
        <div className="error-desc">实际平局但未预测平</div>
      </div>
      <div className="error-card">
        <div className="error-label">弱队低估</div>
        <div className="error-value">{latest.underdog_underestimated_count}</div>
        <div className="error-desc">弱队爆冷但概率过低</div>
      </div>
      <div className="error-card">
        <div className="error-label">过度自信错误</div>
        <div className="error-value">{latest.overconfident_wrong_count}</div>
        <div className="error-desc">高概率预测方向错误</div>
      </div>
    </div>
  );
}

// 3. Market comparison
function MarketComparisonView({ data }: { data: MarketComparisonData | null }) {
  if (!data || data.market_sample_count === 0) return <p className="app-empty-state">暂无市场赔率数据</p>;
  return (
    <div className="market-comparison">
      <div className="comparison-metrics">
        <div className="metric">
          <div className="metric-label">模型 Brier</div>
          <div className={`metric-value ${data.model_brier <= data.market_brier ? "good" : "bad"}`}>
            {fmt(data.model_brier)}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">市场 Brier</div>
          <div className={`metric-value ${data.market_brier <= data.model_brier ? "good" : "bad"}`}>
            {fmt(data.market_brier)}
          </div>
        </div>
        <div className="metric">
          <div className="metric-label">Blend Brier</div>
          <div className={`metric-value ${data.blended_brier <= Math.min(data.model_brier, data.market_brier) ? "good" : ""}`}>
            {fmt(data.blended_brier)}
          </div>
        </div>
      </div>
      <div className="market-summary">
        <span>有市场数据: {data.market_sample_count} 场</span>
        <span>市场有帮助: {data.market_helped_count} 场</span>
        <span>市场有损害: {data.market_hurt_count} 场</span>
        <span>建议市场权重: {data.suggested_market_blend_weight}</span>
      </div>
    </div>
  );
}

// 4. Calibration
function CalibrationView({ buckets }: { buckets: CalibrationBucket[] }) {
  if (!buckets.length) return <p className="app-empty-state">暂无校准数据</p>;
  return (
    <div className="table-wrap">
      <table className="data-table calibration-table">
        <thead>
          <tr><th>概率区间</th><th>样本数</th><th>预测平均</th><th>实际命中率</th><th>校准偏差</th><th>备注</th></tr>
        </thead>
        <tbody>
          {buckets.map((b) => (
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
      <div className="calibration-legend">
        <span>校准偏差 &gt; 0: 过度自信</span>
        <span>校准偏差 &lt; 0: 不够自信</span>
        <span>校准偏差 ≈ 0: 校准良好</span>
      </div>
    </div>
  );
}

// 5. Recommendation
function RecommendationView({ data, insufficientSample }: { data: ModelRecommendation | null; insufficientSample: boolean }) {
  if (!data) return <p className="app-empty-state">暂无推荐</p>;
  return (
    <div className="recommendation-card">
      {insufficientSample ? (
        <>
          <div className="rec-version" style={{ color: "var(--amber)" }}>
            当前默认推荐: <strong>baseline</strong>
          </div>
          <div className="rec-reason" style={{ color: "var(--amber)" }}>
            样本不足，暂不能下结论。在样本量足够之前，建议使用 baseline 模型作为默认选择。
          </div>
          <div className="rec-meta" style={{ opacity: 0.6 }}>
            <span>系统推荐: {data.recommended_model_version}（样本充足后生效）</span>
            <span>置信度: {data.confidence}</span>
            {data.sample_warning && <span className="warn">⚠️ {data.sample_warning}</span>}
            <span>备用: {data.fallback_model_version}</span>
          </div>
        </>
      ) : (
        <>
          <div className="rec-version">
            推荐模型: <strong>{data.recommended_model_version}</strong>
          </div>
          <div className="rec-reason">{data.reason}</div>
          <div className="rec-meta">
            <span>置信度: {data.confidence}</span>
            {data.sample_warning && <span className="warn">⚠️ {data.sample_warning}</span>}
            <span>备用: {data.fallback_model_version}</span>
          </div>
        </>
      )}
    </div>
  );
}

// 6. Data quality
function DataQualityView({ data }: { data: DataQualityReport | null }) {
  if (!data) return <p className="app-empty-state">暂无数据质量报告</p>;
  const icons: Record<string, string> = { pass: "✅", warn: "⚠️", fail: "❌" };
  return (
    <div className="quality-checks">
      <div className="quality-summary">
        总体: {icons[data.summary.overall_status]} {data.summary.pass}通过 / {data.summary.warn}警告 / {data.summary.fail}失败
      </div>
      {data.checks.map((c) => (
        <div key={c.check} className={`quality-item ${c.status}`}>
          <span className="quality-icon">{icons[c.status]}</span>
          <span className="quality-name">{c.check}</span>
          <span className="quality-count">({c.count})</span>
          {c.note && <span className="quality-note">{c.note}</span>}
        </div>
      ))}
    </div>
  );
}

// Main panel
export default function AccuracyPanel() {
  const versions = useQuery({ queryKey: ["model-score-by-version"], queryFn: getModelScoreByVersion });
  const calibration = useQuery({ queryKey: ["model-calibration"], queryFn: getCalibration });
  const market = useQuery({ queryKey: ["market-comparison"], queryFn: getMarketComparison });
  const recommendation = useQuery({ queryKey: ["model-recommendation"], queryFn: getModelRecommendation });
  const quality = useQuery({ queryKey: ["data-quality"], queryFn: getDataQuality });
  const aiEval = useQuery({ queryKey: ["ai-evaluation"], queryFn: getAIEvaluation });

  const versionList = versions.data?.versions ?? [];
  const insufficientSample = versionList.length > 0 && Math.min(...versionList.map(v => v.sample_count)) < 5;

  return (
    <div className="accuracy-panel">
      <Section title="模型版本对比">
        <QueryWrapper isLoading={versions.isLoading} isError={versions.isError} error={versions.error}>
          <VersionTable versions={versionList} />
        </QueryWrapper>
      </Section>

      <Section title="错误归因">
        <QueryWrapper isLoading={versions.isLoading} isError={versions.isError} error={versions.error}>
          <ErrorAttribution versions={versionList} />
        </QueryWrapper>
      </Section>

      <Section title="市场赔率对比">
        <QueryWrapper isLoading={market.isLoading} isError={market.isError} error={market.error}>
          <MarketComparisonView data={market.data ?? null} />
        </QueryWrapper>
      </Section>

      <Section title="概率校准">
        <QueryWrapper isLoading={calibration.isLoading} isError={calibration.isError} error={calibration.error}>
          <CalibrationView buckets={calibration.data?.buckets ?? []} />
        </QueryWrapper>
      </Section>

      <Section title="推荐模型">
        <QueryWrapper isLoading={recommendation.isLoading} isError={recommendation.isError} error={recommendation.error}>
          <RecommendationView data={recommendation.data ?? null} insufficientSample={insufficientSample} />
        </QueryWrapper>
      </Section>

      <section className="accuracy-section">
        <h3>关键摘要</h3>
        <div className="summary-grid-panels">
          <SummaryBlock
            title="当前最佳"
            value={versionList.length ? versionList.reduce((best, v) => v.brier < best.brier ? v : best, versionList[0]).model_version : "暂无"}
            note={versionList.length ? `样本最少 ${Math.min(...versionList.map(v => v.sample_count))} 场` : "暂无评分数据"}
            tone={insufficientSample ? "warn" : "good"}
          />
          <SummaryBlock
            title="AI / Ensemble"
            value={aiEval.data?.ensemble ? (aiEval.data.ensemble.helped > aiEval.data.ensemble.hurt ? "正向" : aiEval.data.ensemble.helped === aiEval.data.ensemble.hurt ? "中性" : "负向") : "暂无"}
            note={aiEval.data?.ensemble ? `帮助 ${aiEval.data.ensemble.helped} / 损害 ${aiEval.data.ensemble.hurt}` : "暂无评估数据"}
            tone={aiEval.data?.ensemble ? (aiEval.data.ensemble.helped > aiEval.data.ensemble.hurt ? "good" : aiEval.data.ensemble.helped === aiEval.data.ensemble.hurt ? "warn" : "bad") : "neutral"}
          />
          <SummaryBlock
            title="市场对比"
            value={market.data?.market_sample_count ? `${market.data.market_sample_count} 场` : "暂无"}
            note={market.data?.market_sample_count ? `建议权重 ${market.data.suggested_market_blend_weight}` : "暂无市场数据"}
            tone={market.data?.market_sample_count ? "good" : "neutral"}
          />
          <SummaryBlock
            title="数据质量"
            value={quality.data ? quality.data.summary.overall_status : "暂无"}
            note={quality.data ? `${quality.data.summary.pass} 通过 / ${quality.data.summary.warn} 警告 / ${quality.data.summary.fail} 失败` : "暂无质量报告"}
            tone={quality.data?.summary.overall_status === "pass" ? "good" : quality.data?.summary.overall_status === "warn" ? "warn" : quality.data?.summary.overall_status === "fail" ? "bad" : "neutral"}
          />
        </div>
      </section>

      <Section title="数据质量">
        <QueryWrapper isLoading={quality.isLoading} isError={quality.isError} error={quality.error}>
          <DataQualityView data={quality.data ?? null} />
        </QueryWrapper>
      </Section>
    </div>
  );
}
