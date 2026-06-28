import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTournamentBracket } from "../api";
import { getTeamDisplayNameFromAny } from "../utils/teamNames";
import SectionCard from "./ui/SectionCard";
import type { BracketMatchup } from "../types";

const STAGE_LABELS: Record<string, string> = {
  round_of_32: "32强", round_of_16: "16强", quarter_final: "四分之一决赛",
  semi_final: "半决赛", third_place: "三四名决赛", final: "决赛",
};

const STAGE_ORDER = ["round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"] as const;

export default function BracketView() {
  const bracket = useQuery({ queryKey: ["bracket"], queryFn: getTournamentBracket });
  const [activeStage, setActiveStage] = useState<string | null>(null);

  if (bracket.isLoading) return <div className="loading-placeholder">加载淘汰赛数据...</div>;
  if (bracket.isError || !bracket.data) return <div className="loading-placeholder">淘汰赛数据暂不可用</div>;

  const data = bracket.data;

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

  return (
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
              {matchups.map((m: BracketMatchup, i: number) => (
                <div key={m.match_position ?? i} className="bracket-card">
                  <div className="bracket-card__teams">
                    <span className="bracket-card__team">
                      {m.home_team ? getTeamDisplayNameFromAny(m.home_team.team_id || m.home_team.team_name) : m.home_source ? getTeamDisplayNameFromAny(m.home_source) : "待定"}
                    </span>
                  </div>
                  <div className="bracket-card__vs">vs</div>
                  <div className="bracket-card__teams">
                    <span className="bracket-card__team">
                      {m.away_team ? getTeamDisplayNameFromAny(m.away_team.team_id || m.away_team.team_name) : m.away_source ? getTeamDisplayNameFromAny(m.away_source) : "待定"}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </SectionCard>
        );
      })()}
    </div>
  );
}
