import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTournamentBracket } from "../api";
import { getTeamDisplayNameFromAny } from "../utils/teamNames";
import SectionCard from "./ui/SectionCard";

const STAGE_LABELS: Record<string, string> = {
  round_of_32: "32强", round_of_16: "16强", quarter_final: "四分之一决赛",
  semi_final: "半决赛", third_place: "三四名决赛", final: "决赛",
};

const STAGE_ORDER = ["round_of_32", "round_of_16", "quarter_final", "semi_final", "third_place", "final"] as const;

export default function BracketView() {
  const bracket = useQuery({ queryKey: ["bracket"], queryFn: getTournamentBracket });
  const [activeStage, setActiveStage] = useState<string | null>(null);

  if (bracket.isLoading) return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>加载淘汰赛数据...</div>;
  if (bracket.isError || !bracket.data) return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>淘汰赛数据暂不可用</div>;

  const data = bracket.data;

  // Find stages with matchups
  const availableStages = STAGE_ORDER.filter(s => {
    const matchups = data[s as keyof typeof data];
    return Array.isArray(matchups) && matchups.length > 0;
  });

  // Auto-select first available stage
  const currentStage = activeStage ?? (availableStages[0] ?? null);

  const hasAnyMatchup = availableStages.length > 0;

  if (!hasAnyMatchup) {
    return (
      <div style={{ textAlign: "center", padding: 40, color: "var(--text-secondary)" }}>
        <p>淘汰赛对阵将在小组赛结束后生成</p>
        <p style={{ fontSize: 12, marginTop: 8 }}>当前为预设赛程，实际对阵取决于小组赛结果</p>
      </div>
    );
  }

  return (
    <div className="bracket-view">
      <div style={{ color: "var(--accent-yellow)", fontSize: 11, marginBottom: 16, padding: "8px 12px", border: "1px solid rgba(246,195,67,0.3)", borderRadius: 4, background: "rgba(246,195,67,0.06)" }}>
        基于 2026 世界杯官方 Match 73-88 框架；最佳第三名落位采用简化贪心分配，与 FIFA 官方组合表可能存在差异。
      </div>

      {/* Stage tabs */}
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

      {/* Current stage matchups */}
      {currentStage && (() => {
        const matchups = data[currentStage as keyof typeof data];
        if (!Array.isArray(matchups) || matchups.length === 0) return null;
        return (
          <SectionCard title={STAGE_LABELS[currentStage] || currentStage} badge={`${matchups.length} 场`}>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 10 }}>
              {matchups.map((m: any) => (
                <div key={m.match_position} style={{ background: "var(--card-bg)", border: "1px solid var(--card-border)", padding: "10px 14px", borderRadius: 4 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 13 }}>
                    <span style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                      {m.home_team ? getTeamDisplayNameFromAny(m.home_team.team_id || m.home_team.team_name) : m.home_source ? getTeamDisplayNameFromAny(m.home_source) : "待定"}
                    </span>
                  </div>
                  <div style={{ textAlign: "center", fontSize: 10, color: "var(--text-secondary)", padding: "2px 0" }}>vs</div>
                  <div style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", fontSize: 13 }}>
                    <span style={{ fontWeight: 500, color: "var(--text-primary)" }}>
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
