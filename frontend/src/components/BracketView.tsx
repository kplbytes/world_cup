import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getDashboard, getMatchDetail, getTournamentBracket } from "../api";
import { getTeamDisplayNameFromAny } from "../utils/teamNames";
import { formatChinaTimeShort } from "../utils/time";
import MatchDetailDrawer from "./MatchDetailDrawer";
import SectionCard from "./ui/SectionCard";
import type { BracketMatchup, EnsemblePredictionSummary, Match } from "../types";

const STAGE_LABELS: Record<string, string> = {
  round_of_32: "32强", round_of_16: "16强", quarter_final: "四分之一决赛",
  semi_final: "半决赛", third_place: "三四名决赛", final: "决赛",
};

const STAGE_ORDER = ["round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"] as const;

// Auto-refresh interval for the bracket view. Live knockout matches
// update every 30s so users see goal / advancement updates without
// manual refresh.
const BRACKET_REFETCH_INTERVAL_MS = 30_000;

function getTeamLabel(team: BracketMatchup["home_team"], source: string | null | undefined): string {
  if (team) return getTeamDisplayNameFromAny(team.team_id || team.team_name);
  if (source) return getTeamDisplayNameFromAny(source);
  return "待定";
}

function getStatusBadge(match: BracketMatchup): { label: string; tone: "final" | "live" | "scheduled" | "pending" } {
  const status = (match.status || "").toLowerCase();
  if (match.home_score != null && match.away_score != null) return { label: "已结束", tone: "final" };
  if (["live", "in_play", "in_progress", "paused"].includes(status)) return { label: "进行中", tone: "live" };
  if (match.is_placeholder_match && !match.home_team && !match.away_team) return { label: "待定", tone: "pending" };
  return { label: "未赛", tone: "scheduled" };
}

function getFooterNote(match: BracketMatchup): string | null {
  const parts: string[] = [];
  if (match.went_to_extra_time) parts.push("加时");
  if (match.went_to_penalties) parts.push("点球");
  if (match.venue) parts.push(match.venue);
  return parts.length > 0 ? parts.join(" · ") : null;
}

function fmtPct(value: number | null | undefined): string {
  if (value == null) return "-";
  if (value < 0.001) return "<0.1%";
  return (value * 100).toFixed(1) + "%";
}

// Compact probability bar for the bracket card. Shows the three-way
// distribution (home / draw / away) plus the advance probabilities
// for knockout matches (where a draw at 90' still produces a winner).
function ProbabilityBar({ ens, isFinished }: { ens: EnsemblePredictionSummary | null; isFinished: boolean }) {
  if (!ens || isFinished) return null;
  const home = Math.max(0, Math.min(1, ens.home_win));
  const draw = Math.max(0, Math.min(1, ens.draw));
  const away = Math.max(0, Math.min(1, ens.away_win));
  const total = home + draw + away;
  if (total <= 0) return null;
  const hPct = (home / total) * 100;
  const dPct = (draw / total) * 100;
  const aPct = (away / total) * 100;
  return (
    <div className="bracket-card__prob-bar">
      <div className="bracket-card__prob-track" title={`主胜 ${fmtPct(home)} · 平 ${fmtPct(draw)} · 客胜 ${fmtPct(away)}`}>
        <span style={{ width: `${hPct}%`, background: "var(--success-green)" }} />
        <span style={{ width: `${dPct}%`, background: "var(--text-secondary)" }} />
        <span style={{ width: `${aPct}%`, background: "var(--accent-yellow)" }} />
      </div>
      <div className="bracket-card__prob-labels">
        <span style={{ color: "var(--success-green)" }}>{fmtPct(home)}</span>
        <span style={{ color: "var(--text-secondary)" }}>{fmtPct(draw)}</span>
        <span style={{ color: "var(--accent-yellow)" }}>{fmtPct(away)}</span>
      </div>
    </div>
  );
}

export default function BracketView() {
  // Bracket structure + auto-refresh (live knockout matches stream
  // goal / advance updates every 30s without manual refresh).
  const bracket = useQuery({
    queryKey: ["bracket"],
    queryFn: getTournamentBracket,
    refetchInterval: BRACKET_REFETCH_INTERVAL_MS,
    refetchIntervalInBackground: false,
  });
  // Dashboard lookup for ensemble predictions on bracket cards.
  // Lightweight: dashboard already has a 30s client-side staleTime, so
  // this does not add extra requests beyond what other views already do.
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: getDashboard, staleTime: 30_000 });
  const [activeStage, setActiveStage] = useState<string | null>(null);
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);
  const [loadingMatchId, setLoadingMatchId] = useState<string | null>(null);

  if (bracket.isLoading) return <div className="loading-placeholder">加载淘汰赛数据...</div>;
  if (bracket.isError || !bracket.data) return <div className="loading-placeholder">淘汰赛数据暂不可用</div>;

  const data = bracket.data;
  // Map match_id -> ensemble prediction summary from dashboard knockout_matches.
  // Falls back to an empty map when dashboard is still loading; bracket cards
  // simply render without probability bars in that case.
  const ensembleByMatchId = new Map<string, EnsemblePredictionSummary>();
  for (const m of dashboard.data?.knockout_matches ?? []) {
    if (m.ensemble_prediction) {
      ensembleByMatchId.set(m.id, m.ensemble_prediction);
    }
  }

  const availableStages = STAGE_ORDER.filter(s => {
    const matchups = data[s as keyof typeof data];
    return Array.isArray(matchups) && matchups.length > 0;
  });

  const currentStage = activeStage ?? (availableStages[0] ?? null);
  const hasAnyMatchup = availableStages.length > 0;

  if (!hasAnyMatchup) {
    return (
      <div className="bracket-disclaimer">
        <p>淘汰赛对阵将在小组赛结束后生成</p>
        <p>当前为预设赛程，实际对阵取决于小组赛结果</p>
      </div>
    );
  }

  const openMatchDetail = async (matchId?: string | null) => {
    if (!matchId || loadingMatchId) return;
    try {
      setLoadingMatchId(matchId);
      const detail = await getMatchDetail(matchId);
      setSelectedMatch(detail);
    } catch (error) {
      console.error("Failed to load knockout match detail", error);
    } finally {
      setLoadingMatchId(null);
    }
  };

  return (
    <>
      <div className="bracket-view">
        <div className="banner-warn">
          基于 2026 世界杯官方 Match 73-104 赛程；最佳第三名落位按官方组合表生成，已结束比赛会自动推进到下一轮。
        </div>

        <div className="bracket-stage-tabs">
          {availableStages.map(stage => (
            <button
              key={stage}
              className={currentStage === stage ? "active" : ""}
              onClick={() => setActiveStage(stage)}
            >
              {STAGE_LABELS[stage] || stage}
            </button>
          ))}
        </div>

        {currentStage && (() => {
          const matchups = data[currentStage as keyof typeof data];
          if (!Array.isArray(matchups) || matchups.length === 0) return null;
          return (
            <SectionCard title={STAGE_LABELS[currentStage] || currentStage} badge={`${matchups.length} 场`}>
              <div className="metric-grid">
                {matchups.map((m: BracketMatchup, i: number) => {
                  const badge = getStatusBadge(m);
                  const footerNote = getFooterNote(m);
                  const canOpen = Boolean(m.id);
                  const isLoading = loadingMatchId === m.id;
                  const isFinished = badge.tone === "final";
                  const ensemble = m.id ? ensembleByMatchId.get(m.id) ?? null : null;
                  const hasPenalties = Boolean(m.went_to_penalties && m.home_penalty_score != null && m.away_penalty_score != null);
                  const penaltyLabel = hasPenalties ? `点球 ${m.home_penalty_score}-${m.away_penalty_score}` : null;
                  return (
                    <button
                      key={m.id ?? m.match_position ?? i}
                      type="button"
                      className="bracket-card bracket-card--interactive"
                      onClick={() => openMatchDetail(m.id)}
                      disabled={!canOpen || isLoading}
                    >
                      <div className="bracket-card__header">
                        <span className="bracket-card__match">Match {m.match_number ?? m.match_position ?? i + 1}</span>
                        <span className={`bracket-card__status bracket-card__status--${badge.tone}`}>{badge.label}</span>
                      </div>
                      <div className="bracket-card__meta">
                        <span>{m.round_name || STAGE_LABELS[m.stage] || m.stage}</span>
                        <span>{formatChinaTimeShort(m.kickoff)}</span>
                      </div>
                      <div className="bracket-card__team-row">
                        <span className="bracket-card__team">{getTeamLabel(m.home_team, m.home_source)}</span>
                        <span className="bracket-card__team-side">
                          {m.home_advance ? <span className="bracket-card__advance">晋级</span> : null}
                          {m.home_score != null ? <strong className="bracket-card__score">{m.home_score}</strong> : null}
                          {hasPenalties && m.home_penalty_score != null ? (
                            <span className="bracket-card__penalty">（{m.home_penalty_score}）</span>
                          ) : null}
                        </span>
                      </div>
                      <div className="bracket-card__vs">vs</div>
                      <div className="bracket-card__team-row">
                        <span className="bracket-card__team">{getTeamLabel(m.away_team, m.away_source)}</span>
                        <span className="bracket-card__team-side">
                          {m.away_advance ? <span className="bracket-card__advance">晋级</span> : null}
                          {m.away_score != null ? <strong className="bracket-card__score">{m.away_score}</strong> : null}
                          {hasPenalties && m.away_penalty_score != null ? (
                            <span className="bracket-card__penalty">（{m.away_penalty_score}）</span>
                          ) : null}
                        </span>
                      </div>
                      <ProbabilityBar ens={ensemble} isFinished={isFinished} />
                      <div className="bracket-card__footer">
                        {penaltyLabel ?? footerNote ?? "点击查看比赛详情"}
                        {isLoading ? " · 加载中..." : ""}
                      </div>
                    </button>
                  );
                })}
              </div>
            </SectionCard>
          );
        })()}
      </div>
      <MatchDetailDrawer open={selectedMatch != null} match={selectedMatch} onClose={() => setSelectedMatch(null)} />
    </>
  );
}
