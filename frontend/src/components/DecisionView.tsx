import { useQuery } from "@tanstack/react-query";
import { getDecision, getModelScore } from "../api";
import type { DecisionMatch, ReviewMatch, DecisionIntelligenceRisk } from "../types";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { formatChinaTimeShort, isFinishedMatch } from "../utils/time";

function DecisionCard({ match, showMarket }: { match: DecisionMatch; showMarket?: boolean }) {
  const kickoff = formatChinaTimeShort(match.kickoff);
  const pred = match.prediction;
  const maxProb = pred ? Math.max(pred.home_win, pred.draw, pred.away_win) : null;
  const favorite = pred ? (pred.home_win === maxProb ? "home" : pred.draw === maxProb ? "draw" : "away") : null;
  return <div className="decision-card">
    <div className="decision-card-header">
      <span className="decision-group">{match.group_code}组</span>
      <span className="decision-kickoff">{kickoff}</span>
    </div>
    <div className="decision-teams">
      <span className={`decision-team ${favorite === "home" ? "fav" : ""}`}>{match.home_team.flag} {getTeamDisplayFromRef(match.home_team)}</span>
      {isFinishedMatch(match)
        ? <span className="decision-score">{match.home_score} : {match.away_score}</span>
        : <span className="decision-vs">VS</span>}
      <span className={`decision-team ${favorite === "away" ? "fav" : ""}`}>{getTeamDisplayFromRef(match.away_team)} {match.away_team.flag}</span>
    </div>
    {pred && <div className="decision-probs">
      {match.numerical_enabled && pred.base_home_win != null ? (
        <div className="numerical-probs-comparison">
          <div className="prob-row base-prob">
            <span className="prob-label">基础</span>
            <span>{(pred.base_home_win * 100).toFixed(0)}%</span>
            <span>{(pred.base_draw! * 100).toFixed(0)}%</span>
            <span>{(pred.base_away_win! * 100).toFixed(0)}%</span>
          </div>
          <div className="prob-row adjusted-prob">
            <span className="prob-label">修正</span>
            <span className={favorite === "home" ? "active" : ""}>{(pred.home_win * 100).toFixed(0)}%</span>
            <span className={favorite === "draw" ? "active" : ""}>{(pred.draw * 100).toFixed(0)}%</span>
            <span className={favorite === "away" ? "active" : ""}>{(pred.away_win * 100).toFixed(0)}%</span>
          </div>
        </div>
      ) : (
        <>
          <span className={favorite === "home" ? "active" : ""}>{(pred.home_win * 100).toFixed(0)}%</span>
          <span className={favorite === "draw" ? "active" : ""}>{(pred.draw * 100).toFixed(0)}%</span>
          <span className={favorite === "away" ? "active" : ""}>{(pred.away_win * 100).toFixed(0)}%</span>
        </>
      )}
    </div>}
    {showMarket && match.market?.divergence && <div className={`decision-divergence ${match.market.divergence.level}`}>
      分歧 {(match.market.divergence.max_divergence * 100).toFixed(0)}% · {match.market.divergence.level}
    </div>}
  </div>;
}

function ReviewCard({ match }: { match: ReviewMatch }) {
  const pred = match.prediction;
  const review = match.review;
  return <div className={`review-card ${match.snapshot?.outcome_correct ? "correct" : "incorrect"}`}>
    <div className="decision-teams">
      <span className="decision-team">{match.home_team.flag} {getTeamDisplayFromRef(match.home_team)}</span>
      <span className="decision-score">{match.home_score} : {match.away_score}</span>
      <span className="decision-team">{getTeamDisplayFromRef(match.away_team)} {match.away_team.flag}</span>
    </div>
    {match.snapshot && pred && <div className="review-detail">
      <span>预测：{pred.home_win > pred.draw && pred.home_win > pred.away_win ? "主胜" : pred.away_win > pred.draw ? "客胜" : "平局"} ({(Math.max(pred.home_win, pred.draw, pred.away_win) * 100).toFixed(0)}%)</span>
      <span className={match.snapshot.outcome_correct ? "hit" : "miss"}>{match.snapshot.outcome_correct ? "命中" : "偏差"}</span>
    </div>}
    {review && <div className="review-metrics">
      <span>Brier {review.brier.toFixed(3)}</span>
      <span>LogLoss {review.log_loss.toFixed(3)}</span>
      <span>xG误差 {review.xg_error.toFixed(2)}</span>
    </div>}
    {review && <p className="review-bias">{review.bias_explanation}</p>}
  </div>;
}

function IntelligenceRiskCard({ risk }: { risk: DecisionIntelligenceRisk }) {
  const kickoff = formatChinaTimeShort(risk.kickoff);
  return <div className="decision-card risk-card">
    <div className="decision-card-header">
      <span className="decision-group">{risk.provider}</span>
      <span className="decision-kickoff">{kickoff}</span>
    </div>
    <div className="decision-teams">
      <span className="decision-team">{risk.home_team.flag} {getTeamDisplayFromRef(risk.home_team)}</span>
      <span className="decision-vs">VS</span>
      <span className="decision-team">{getTeamDisplayFromRef(risk.away_team)} {risk.away_team.flag}</span>
    </div>
    <div className="risk-detail">
      <span className={`risk-level ${risk.level === "高" ? "high" : risk.level === "中" ? "medium" : "low"}`}>风险: {risk.level}</span>
      <span className="risk-type">{risk.risk_type}</span>
    </div>
    <p className="risk-reason">{risk.reason}</p>
  </div>;
}

function Section({ title, children, empty }: { title: string; children: React.ReactNode; empty?: string }) {
  return <section className="decision-section">
    <h3>{title}</h3>
    {children}
  </section>;
}

function formatDelta(value: number, inverse?: boolean) {
  const adjusted = inverse ? -value : value;
  const prefix = adjusted > 0 ? "+" : "";
  return `${prefix}${adjusted.toFixed(3)}`;
}

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

export default function DecisionView() {
  const query = useQuery({ queryKey: ["decision"], queryFn: getDecision });
  const modelScoreQuery = useQuery({ queryKey: ["model-score"], queryFn: getModelScore });
  if (query.isLoading) return <div className="app-empty-state">正在加载决策数据</div>;
  if (query.isError || !query.data) return <div className="app-empty-state error">决策数据加载失败</div>;
  const d = query.data;
  const modelScore = modelScoreQuery.data;
  const reviewSummary = d.review_summary ?? {
    matches_scored: 0,
    brier_score: 0,
    log_loss: 0,
    outcome_hit_rate: 0,
    top_score_hit_rate: 0,
    xg_mae: 0,
  };
  const todayCount = d.today_matches.length;
  const confidentCount = d.most_confident.length;
  const uncertainCount = d.most_uncertain.length;
  const divergenceCount = d.biggest_divergence.length;
  const upsetCount = d.upset_risk.length;
  const reviewCount = reviewSummary.matches_scored;
  return <div className="decision-view">
    <section className="decision-section">
      <h3>关键摘要</h3>
      <div className="summary-grid-panels">
        <SummaryBlock
          title="今日重点"
          value={`${todayCount} 场`}
          note={todayCount > 0 ? "正在关注的比赛" : "今明两天暂无开赛"}
          tone={todayCount > 0 ? "good" : "neutral"}
        />
        <SummaryBlock
          title="最有把握"
          value={`${confidentCount} 场`}
          note={confidentCount > 0 ? "更偏向确定性" : "暂无"}
          tone={confidentCount > 0 ? "good" : "neutral"}
        />
        <SummaryBlock
          title="最纠结"
          value={`${uncertainCount} 场`}
          note={uncertainCount > 0 ? "分歧更大，需要谨慎" : "暂无"}
          tone={uncertainCount > 0 ? "warn" : "neutral"}
        />
        <SummaryBlock
          title="赛后复盘"
          value={`${reviewCount} 场`}
          note={reviewCount > 0 ? `Brier ${reviewSummary.brier_score.toFixed(3)} · LogLoss ${reviewSummary.log_loss.toFixed(3)}` : "暂无终场样本"}
          tone={reviewCount > 0 ? "good" : "neutral"}
        />
      </div>
    </section>
    <Section title="情报风险提示">
      {(!d.intelligence_risks || d.intelligence_risks.length === 0)
        ? <p className="app-empty-state">暂无赛前情报风险</p>
        : <div className="decision-grid">{d.intelligence_risks.map((r, i) => <IntelligenceRiskCard key={`${r.match_id}-${i}`} risk={r} />)}</div>}
    </Section>
    <Section title="今日重点比赛">
      {d.today_matches.length === 0
        ? <p className="app-empty-state">今明两天暂无开赛比赛</p>
        : <div className="decision-grid">{d.today_matches.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型最有把握">
      {d.most_confident.length === 0
        ? <p className="app-empty-state">暂无数据</p>
        : <div className="decision-grid">{d.most_confident.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型最纠结">
      {d.most_uncertain.length === 0
        ? <p className="app-empty-state">暂无数据</p>
        : <div className="decision-grid">{d.most_uncertain.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型与市场分歧最大">
      {d.biggest_divergence.length === 0
        ? <p className="app-empty-state">暂无市场赔率数据：Sporttery 当前未返回可匹配比赛</p>
        : <div className="decision-grid">{d.biggest_divergence.map((m) => <DecisionCard key={m.id} match={m} showMarket />)}</div>}
    </Section>
    <Section title="冷门风险提示">
      {d.upset_risk.length === 0
        ? <p className="app-empty-state">暂无高冷门风险比赛</p>
        : <div className="decision-grid">{d.upset_risk.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="赛后复盘">
      {reviewSummary.matches_scored > 0 && <div className="review-summary">
        <span>样本 {reviewSummary.matches_scored}</span>
        <span>Brier {reviewSummary.brier_score.toFixed(3)}</span>
        <span>LogLoss {reviewSummary.log_loss.toFixed(3)}</span>
        <span>胜平负命中率 {(reviewSummary.outcome_hit_rate * 100).toFixed(0)}%</span>
        <span>比分命中率 {(reviewSummary.top_score_hit_rate * 100).toFixed(0)}%</span>
        <span>xG误差 {reviewSummary.xg_mae.toFixed(2)}</span>
      </div>}
      {d.recent_review.length === 0
        ? <p className="app-empty-state">昨日暂无终场比赛</p>
        : <div className="decision-grid">{d.recent_review.map((m) => <ReviewCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型版本迭代">
      {modelScoreQuery.isLoading
        ? <p className="app-empty-state">正在加载版本评分</p>
        : !modelScore || modelScore.history.length === 0
          ? <p className="app-empty-state">暂无已终场评分样本</p>
          : <>
              {modelScore.comparison
                ? <div className="version-compare">
                    <div className="version-compare-card">
                      <strong>{modelScore.comparison.current_version.model_version}</strong>
                      <span>对比基线：{modelScore.comparison.previous_version.model_version}</span>
                    </div>
                    <div className="version-deltas">
                      <span>Brier {formatDelta(modelScore.comparison.deltas.brier_score, true)}</span>
                      <span>LogLoss {formatDelta(modelScore.comparison.deltas.log_loss, true)}</span>
                      <span>胜平负命中率 {formatDelta(modelScore.comparison.deltas.outcome_hit_rate * 100)}%</span>
                      <span>比分命中率 {formatDelta(modelScore.comparison.deltas.top_score_hit_rate * 100)}%</span>
                      <span>xG误差 {formatDelta(modelScore.comparison.deltas.xg_mae, true)}</span>
                    </div>
                  </div>
                : <p className="app-empty-state">当前只有一个模型版本的评分样本，后续版本会在这里对比。</p>}
              <div className="version-history">
                {modelScore.model_versions.map((item) => <div className="version-card" key={item.model_version}>
                  <div className="version-card-header">
                    <strong>{item.model_version}</strong>
                    <span>{item.runs} 次评分 / {item.total_matches_scored} 场样本</span>
                  </div>
                  <div className="review-summary">
                    <span>平均 Brier {item.average_brier_score.toFixed(3)}</span>
                    <span>平均 LogLoss {item.average_log_loss.toFixed(3)}</span>
                    <span>平均命中率 {(item.average_outcome_hit_rate * 100).toFixed(0)}%</span>
                    <span>平均 xG误差 {item.average_xg_mae.toFixed(2)}</span>
                  </div>
                  <p className="version-latest">最近 revision {String(item.latest.revision_id).padStart(3, "0")} · {new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(item.latest.created_at))}</p>
                </div>)}
              </div>
            </>}
    </Section>
  </div>;
}
