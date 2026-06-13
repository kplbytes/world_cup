import { useState } from "react";
import type { Match } from "../types";
import ProbabilityBar from "./ProbabilityBar";

const confidenceClass: Record<string, string> = { "高": "high", "中": "medium", "低": "low" };

export default function MatchCard({ match }: { match: Match }) {
  const [open, setOpen] = useState(false);
  const kickoff = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(match.kickoff));
  const manualAdjustments = match.manual_adjustments ?? [];
  return <article className={`match-card ${open ? "expanded" : ""}`} data-testid="match-card">
    <button className="match-summary" onClick={() => setOpen(!open)} aria-expanded={open}>
      <span className="match-meta">{kickoff}<small>{match.venue ?? "场地待定"}</small></span>
      <span className="team home"><i>{match.home_team.flag}</i>{match.home_team.short_name}</span>
      <span className="versus">{match.status === "final" ? `${match.home_score} : ${match.away_score}` : "VS"}</span>
      <span className="team away"><i>{match.away_team.flag}</i>{match.away_team.short_name}</span>
      <span className={`confidence ${confidenceClass[match.prediction?.confidence_label ?? ""] ?? "final"}`}>{match.status === "final" ? "终场" : match.prediction?.confidence_label}</span>
    </button>
    <div className="match-detail"><div>
      {match.prediction ? <>
        <div className="probability-block">
          <ProbabilityBar label="主胜" value={match.prediction.home_win} />
          <ProbabilityBar label="平局" value={match.prediction.draw} tone="amber" />
          <ProbabilityBar label="客胜" value={match.prediction.away_win} tone="coral" />
        </div>
        <div className="analysis-copy">
          <p>{match.prediction.explanation}</p>
          <div className="scoreline-list"><b>xG {match.prediction.home_xg.toFixed(2)} – {match.prediction.away_xg.toFixed(2)}</b>{match.prediction.scorelines.map((score) => <span key={`${score.home_goals}-${score.away_goals}`}>{score.home_goals}:{score.away_goals} <small>{(score.probability * 100).toFixed(1)}%</small></span>)}</div>
        </div>
        {match.prediction.data_confidence != null && <div className="confidence-panel">
          <ProbabilityBar label="数据置信度" value={match.prediction.data_confidence} />
          <ProbabilityBar label="模型置信度" value={match.prediction.model_confidence ?? 0} tone={match.prediction.model_confidence_label === "高" ? undefined : match.prediction.model_confidence_label === "中" ? "amber" : "coral"} />
        </div>}
        {manualAdjustments.length > 0 && <div className="adjustment-panel">
          <h4>人工修正</h4>
          {manualAdjustments.map((adjustment) => <div className="adjustment-row" key={adjustment.id}>
            <strong>{adjustment.affected_team_name} · {adjustment.adjustment_type}</strong>
            <span>进攻 {adjustment.attack_delta > 0 ? "+" : ""}{adjustment.attack_delta.toFixed(2)} / 防守 {adjustment.defense_delta > 0 ? "+" : ""}{adjustment.defense_delta.toFixed(2)}</span>
            <p>{adjustment.note}</p>
          </div>)}
        </div>}
        {match.market && <div className="divergence-panel">
          <h4>模型校准参考 {match.market.divergence && <small className={`divergence-level ${match.market.divergence.level}`}>分歧：{match.market.divergence.level}</small>}</h4>
          <div className="calibration-row"><span>主胜</span><b>{(match.prediction.home_win * 100).toFixed(0)}%</b><small>vs</small><b>{(match.market.home_probability * 100).toFixed(0)}%</b>{match.market.divergence && <span className={`diff ${match.market.divergence.home_diff > 0 ? "pos" : "neg"}`}>{match.market.divergence.home_diff > 0 ? "+" : ""}{(match.market.divergence.home_diff * 100).toFixed(1)}%</span>}</div>
          <div className="calibration-row"><span>平局</span><b>{(match.prediction.draw * 100).toFixed(0)}%</b><small>vs</small><b>{(match.market.draw_probability * 100).toFixed(0)}%</b>{match.market.divergence && <span className={`diff ${match.market.divergence.draw_diff > 0 ? "pos" : "neg"}`}>{match.market.divergence.draw_diff > 0 ? "+" : ""}{(match.market.divergence.draw_diff * 100).toFixed(1)}%</span>}</div>
          <div className="calibration-row"><span>客胜</span><b>{(match.prediction.away_win * 100).toFixed(0)}%</b><small>vs</small><b>{(match.market.away_probability * 100).toFixed(0)}%</b>{match.market.divergence && <span className={`diff ${match.market.divergence.away_diff > 0 ? "pos" : "neg"}`}>{match.market.divergence.away_diff > 0 ? "+" : ""}{(match.market.divergence.away_diff * 100).toFixed(1)}%</span>}</div>
          {match.market.divergence?.level === "高" && <p className="divergence-hint">建议人工核查：伤停、轮换、战意、赛程密度和预计首发是否与市场预期存在明显偏差。</p>}
        </div>}
      </> : <p className="final-note">终场结果已锁定并计入积分与后续预测。</p>}
    </div></div>
  </article>;
}
