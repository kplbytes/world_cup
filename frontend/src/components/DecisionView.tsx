import { useQuery } from "@tanstack/react-query";
import { getDecision } from "../api";
import type { DecisionMatch, ReviewMatch } from "../types";

function DecisionCard({ match, showMarket }: { match: DecisionMatch; showMarket?: boolean }) {
  const kickoff = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(match.kickoff));
  const pred = match.prediction;
  const maxProb = pred ? Math.max(pred.home_win, pred.draw, pred.away_win) : null;
  const favorite = pred ? (pred.home_win === maxProb ? "home" : pred.draw === maxProb ? "draw" : "away") : null;
  return <div className="decision-card">
    <div className="decision-card-header">
      <span className="decision-group">{match.group_code}组</span>
      <span className="decision-kickoff">{kickoff}</span>
    </div>
    <div className="decision-teams">
      <span className={`decision-team ${favorite === "home" ? "fav" : ""}`}>{match.home_team.flag} {match.home_team.short_name}</span>
      {match.status === "final"
        ? <span className="decision-score">{match.home_score} : {match.away_score}</span>
        : <span className="decision-vs">VS</span>}
      <span className={`decision-team ${favorite === "away" ? "fav" : ""}`}>{match.away_team.short_name} {match.away_team.flag}</span>
    </div>
    {pred && <div className="decision-probs">
      <span className={favorite === "home" ? "active" : ""}>{(pred.home_win * 100).toFixed(0)}%</span>
      <span className={favorite === "draw" ? "active" : ""}>{(pred.draw * 100).toFixed(0)}%</span>
      <span className={favorite === "away" ? "active" : ""}>{(pred.away_win * 100).toFixed(0)}%</span>
    </div>}
    {showMarket && match.market?.divergence && <div className={`decision-divergence ${match.market.divergence.level}`}>
      分歧 {(match.market.divergence.max_divergence * 100).toFixed(0)}% · {match.market.divergence.level}
    </div>}
  </div>;
}

function ReviewCard({ match }: { match: ReviewMatch }) {
  const pred = match.prediction;
  return <div className={`review-card ${match.snapshot?.outcome_correct ? "correct" : "incorrect"}`}>
    <div className="decision-teams">
      <span className="decision-team">{match.home_team.flag} {match.home_team.short_name}</span>
      <span className="decision-score">{match.home_score} : {match.away_score}</span>
      <span className="decision-team">{match.away_team.short_name} {match.away_team.flag}</span>
    </div>
    {match.snapshot && pred && <div className="review-detail">
      <span>预测：{pred.home_win > pred.draw && pred.home_win > pred.away_win ? "主胜" : pred.away_win > pred.draw ? "客胜" : "平局"} ({(Math.max(pred.home_win, pred.draw, pred.away_win) * 100).toFixed(0)}%)</span>
      <span className={match.snapshot.outcome_correct ? "hit" : "miss"}>{match.snapshot.outcome_correct ? "命中" : "偏差"}</span>
    </div>}
  </div>;
}

function Section({ title, children, empty }: { title: string; children: React.ReactNode; empty?: string }) {
  return <section className="decision-section">
    <h3>{title}</h3>
    {children}
  </section>;
}

export default function DecisionView() {
  const query = useQuery({ queryKey: ["decision"], queryFn: getDecision });
  if (query.isLoading) return <div className="state-screen"><span>正在加载决策数据</span></div>;
  if (query.isError || !query.data) return <div className="state-screen error"><span>决策数据加载失败</span></div>;
  const d = query.data;
  return <div className="decision-view">
    <Section title="今日重点比赛">
      {d.today_matches.length === 0
        ? <p className="decision-empty">今明两天暂无开赛比赛</p>
        : <div className="decision-grid">{d.today_matches.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型最有把握">
      {d.most_confident.length === 0
        ? <p className="decision-empty">暂无数据</p>
        : <div className="decision-grid">{d.most_confident.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型最纠结">
      {d.most_uncertain.length === 0
        ? <p className="decision-empty">暂无数据</p>
        : <div className="decision-grid">{d.most_uncertain.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="模型与市场分歧最大">
      {d.biggest_divergence.length === 0
        ? <p className="decision-empty">暂无体彩赔率数据</p>
        : <div className="decision-grid">{d.biggest_divergence.map((m) => <DecisionCard key={m.id} match={m} showMarket />)}</div>}
    </Section>
    <Section title="冷门风险提示">
      {d.upset_risk.length === 0
        ? <p className="decision-empty">暂无数据</p>
        : <div className="decision-grid">{d.upset_risk.map((m) => <DecisionCard key={m.id} match={m} />)}</div>}
    </Section>
    <Section title="赛后复盘">
      {d.recent_review.length === 0
        ? <p className="decision-empty">昨日暂无终场比赛</p>
        : <div className="decision-grid">{d.recent_review.map((m) => <ReviewCard key={m.id} match={m} />)}</div>}
    </Section>
  </div>;
}
