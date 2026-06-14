import { useQuery } from "@tanstack/react-query";
import type { Match, AIPredictionItem, EnsemblePredictionItem } from "../types";
import { getAIPredictions, getEnsemble } from "../api";
import { formatChinaTimeShort, isFinishedMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { getMatchRecommendation, getMatchRecommendationLabel, getSourceDisplayName } from "../utils/recommendation";
import type { RecommendationSource } from "../utils/recommendation";
import RiskBadge from "./ui/RiskBadge";
import ProbabilityBars from "./ui/ProbabilityBars";

type Props = {
  match: Match;
  onOpenDetails?: (match: Match) => void;
  detailsOpen?: boolean;
};

function sourceLabel(source: RecommendationSource): string {
  const name = getSourceDisplayName(source);
  return name ? `来源：${name}` : "";
}

export default function MatchSummaryCard({ match, onOpenDetails, detailsOpen = false }: Props) {
  const kickoff = formatChinaTimeShort(match.kickoff);
  const homeName = getTeamDisplayFromRef(match.home_team);
  const awayName = getTeamDisplayFromRef(match.away_team);

  // Query AI predictions and Ensemble for this match (shared cache with drawer)
  const aiQuery = useQuery({
    queryKey: ["ai-predictions", match.id],
    queryFn: () => getAIPredictions(match.id),
    staleTime: 60_000,
  });

  const ensembleQuery = useQuery({
    queryKey: ["ensemble", match.id],
    queryFn: () => getEnsemble(match.id),
    staleTime: 60_000,
  });

  const aiPredictions: AIPredictionItem[] = aiQuery.data?.predictions ?? [];
  const ensembleItem: EnsemblePredictionItem | null = ensembleQuery.data?.predictions?.[0] ?? null;

  // Unified recommendation
  const rec = getMatchRecommendation(match, aiPredictions, ensembleItem);
  const recLabel = getMatchRecommendationLabel(rec, homeName, awayName);

  // Risk level - use rec probabilities when available
  const pred = match.prediction;
  const riskLevel = pred
    ? pred.confidence_label === "低"
      ? "high"
      : pred.confidence_label === "中"
        ? "medium"
        : "low"
    : "medium";

  // Snapshot status
  let snapshotDotClass = "none";
  let snapshotText = "无赛前快照";
  if (match.snapshot_status?.locked) {
    snapshotDotClass = "ready";
    snapshotText = "赛前快照已保存";
  } else if (match.snapshot_status?.real_time_only) {
    snapshotDotClass = "realtime";
    snapshotText = "实时预测";
  }

  // Status label for finished matches
  const isFinished = isFinishedMatch(match);
  const statusLabel = isFinished
    ? "终场"
    : match.status === "live"
      ? "进行中"
      : null;

  // Match status description for finished matches with predictions
  let matchStatusNote = "";
  if (isFinished && rec.valid) {
    matchStatusNote = match.snapshot_status?.locked
      ? "已完赛 / 赛前预测已生成"
      : match.snapshot_status?.real_time_only
        ? "已完赛 / 实时预测"
        : "已完赛";
  } else if (isFinished && !rec.valid) {
    matchStatusNote = match.snapshot_status?.participates_in_model_score
      ? "已完赛"
      : "缺少赛前预测快照，不参与评分";
  }

  const handleOpen = () => onOpenDetails?.(match);

  return (
    <article className="match-summary-card" data-testid="match-summary-card">
      {/* Row 1: Time + Risk + Status */}
      <div className="msc-header">
        <span className="msc-time">{kickoff}</span>
        <RiskBadge level={riskLevel} />
        {statusLabel && (
          <span className="msc-status final">{statusLabel}</span>
        )}
        {!isFinished && match.status !== "live" && (
          <span className="msc-snapshot">
            <span className={`msc-snapshot-dot ${snapshotDotClass}`} />
            {snapshotText}
          </span>
        )}
      </div>

      {/* Row 2: Home vs Away */}
      <div className="msc-teams">
        <div className="msc-team-row">
          <span className="msc-team-flag">{match.home_team.flag}</span>
          <span className="msc-team-name" title={homeName}>{homeName}</span>
        </div>
        <div className="msc-versus">
          {isFinished && match.home_score != null && match.away_score != null ? `${match.home_score} : ${match.away_score}` : "vs"}
        </div>
        <div className="msc-team-row">
          <span className="msc-team-flag">{match.away_team.flag}</span>
          <span className="msc-team-name" title={awayName}>{awayName}</span>
        </div>
      </div>

      {/* Row 3: Decision Summary */}
      <div className="msc-decision">
        <div className="msc-decision-main">
          <span className="msc-rec-label">推荐：</span>
          <span className="msc-rec-value">{recLabel}</span>
          {rec.valid && sourceLabel(rec.source) && (
            <span className="msc-rec-source" style={{ fontSize: 11, color: "var(--text-secondary)", marginLeft: 6 }}>
              {sourceLabel(rec.source)}
            </span>
          )}
        </div>
        {rec.valid && (
          <div className="msc-decision-probs">
            概率：{Math.round(rec.homeWin * 100)}% / {Math.round(rec.draw * 100)}% / {Math.round(rec.awayWin * 100)}%
          </div>
        )}
        {pred && match.prediction?.scorelines && match.prediction.scorelines.length > 0 && (
          <div className="msc-decision-scoreline">
            比分倾向：{match.prediction.scorelines.slice(0, 2).map(s => `${s.home_goals}-${s.away_goals}`).join(" 或 ")}
          </div>
        )}
        {!rec.valid && !pred && (
          <div className="msc-decision-probs" style={{ color: "var(--text-secondary)" }}>
            比分待生成
          </div>
        )}
        {matchStatusNote && (
          <div className="msc-decision-status" style={{ fontSize: 11, color: "var(--text-secondary)" }}>
            {matchStatusNote}
          </div>
        )}
        <div className="msc-decision-snapshot">
          快照：{match.snapshot_status?.locked ? "已保存" : match.snapshot_status?.real_time_only ? "实时" : "无赛前快照"}
        </div>
      </div>

      {/* Row 4: Probability Bars */}
      {rec.valid && (
        <ProbabilityBars
          homeWin={rec.homeWin}
          draw={rec.draw}
          awayWin={rec.awayWin}
        />
      )}

      {/* Row 5: Detail button */}
      <button className="msc-detail-btn" onClick={handleOpen}>
        {detailsOpen ? "详情已打开" : "查看分析"}
      </button>
    </article>
  );
}
