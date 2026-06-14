import type { Match } from "../types";
import ProbabilityBar from "./ProbabilityBar";
import { formatChinaTimeShort, isFinishedMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { directionLabel } from "../utils/recommendation";

const confidenceClass: Record<string, string> = { "高": "high", "中": "medium", "低": "low" };

type Props = {
  match: Match;
  onOpenDetails?: (match: Match) => void;
  detailsOpen?: boolean;
};

export default function MatchCard({ match, onOpenDetails, detailsOpen = false }: Props) {
  const kickoff = formatChinaTimeShort(match.kickoff);
  const pred = match.prediction;
  const isFinished = isFinishedMatch(match);
  const hasBaseline = pred?.base_home_win != null;
  const locked = match.snapshot_status?.locked ?? false;
  const risk = pred
    ? pred.confidence_label === "低"
      ? { text: "高风险", tone: "high" }
      : pred.confidence_label === "中"
        ? { text: "中风险", tone: "medium" }
        : { text: "低风险", tone: "low" }
    : { text: "风险待确认", tone: "medium" };

  const handleOpen = () => onOpenDetails?.(match);

  return (
    <article className={`match-card ${detailsOpen ? "active" : ""}`} data-testid="match-card">
      <div className="match-summary">
        <span className="match-meta">{kickoff}<small>{match.venue ?? "场地待定"}</small></span>
        <span className="team home"><i>{match.home_team.flag}</i>{getTeamDisplayFromRef(match.home_team)}</span>
        <span className="versus">{isFinished && match.home_score != null && match.away_score != null ? `${match.home_score} : ${match.away_score}` : "VS"}</span>
        <span className="team away"><i>{match.away_team.flag}</i>{getTeamDisplayFromRef(match.away_team)}</span>
        <span className={`confidence ${confidenceClass[pred?.confidence_label ?? ""] ?? "final"}`}>{isFinished ? "终场" : pred?.confidence_label ?? "待预测"}</span>
      </div>

      <div className="match-summary-body">
        <div className="summary-grid">
          <div><span>Baseline</span><strong>{pred ? directionLabel(pred.base_home_win ?? pred.home_win, pred.base_draw ?? pred.draw, pred.base_away_win ?? pred.away_win) : "待生成"}</strong></div>
          <div><span>AI</span><strong>{pred ? directionLabel(pred.home_win, pred.draw, pred.away_win) : "待运行"}</strong></div>
          <div><span>Ensemble</span><strong>{pred ? directionLabel(pred.home_win, pred.draw, pred.away_win) : "待生成"}</strong></div>
          <div><span>预测比分</span><strong>{pred ? `${pred.home_xg.toFixed(1)} : ${pred.away_xg.toFixed(1)}` : "待生成"}</strong></div>
          <div><span>风险等级</span><strong className={risk.tone}>{risk.text}</strong></div>
          <div><span>锁定</span><strong>{locked ? "已锁定" : match.snapshot_status?.real_time_only ? "实时" : "未锁定"}</strong></div>
        </div>

        {pred && (
          <div className="probability-block compact">
            <ProbabilityBar label="主胜" value={pred.home_win} />
            <ProbabilityBar label="平局" value={pred.draw} tone="amber" />
            <ProbabilityBar label="客胜" value={pred.away_win} tone="coral" />
          </div>
        )}

        <button className="detail-toggle-btn" onClick={handleOpen}>
          {detailsOpen ? "详情已打开" : "查看详情"}
        </button>
      </div>
    </article>
  );
}
