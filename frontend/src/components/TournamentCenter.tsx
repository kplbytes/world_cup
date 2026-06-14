import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTournamentProjections, getTournamentBracket } from "../api";
import type { TeamProjection } from "../types";
import { getTeamZhName } from "../utils/teamNames";
import BracketView from "./BracketView";
import SectionCard from "./ui/SectionCard";

type TabKey = "champion" | "qualification" | "bracket";

const TABS: { key: TabKey; label: string }[] = [
  { key: "champion", label: "冠军概率" },
  { key: "qualification", label: "晋级概率" },
  { key: "bracket", label: "淘汰赛路径" },
];

function fmtPct(value: number): string {
  if (value < 0.001) return "<0.1%";
  return (value * 100).toFixed(1) + "%";
}

type ChampionTier = "hot" | "contender" | "darkhorse" | "other";

function getChampionTier(champion: number): ChampionTier {
  if (champion > 0.05) return "hot";
  if (champion > 0.02) return "contender";
  if (champion > 0.005) return "darkhorse";
  return "other";
}

const TIER_CONFIG: Record<ChampionTier, { label: string; subtitle: string; cssClass: string; color: string }> = {
  hot: { label: "争冠热门", subtitle: ">5%", cssClass: "champion-category__title--hot", color: "var(--success-green)" },
  contender: { label: "有力竞争者", subtitle: "2-5%", cssClass: "champion-category__title--contender", color: "var(--accent-yellow)" },
  darkhorse: { label: "潜在黑马", subtitle: "0.5-2%", cssClass: "champion-category__title--darkhorse", color: "var(--risk-red)" },
  other: { label: "其他", subtitle: "<0.5%", cssClass: "champion-category__title--other", color: "var(--text-secondary)" },
};

function extractGroup(teamId: string): string {
  const match = teamId.match(/_([A-Z])$/);
  return match ? match[1] : "?";
}

export default function TournamentCenter() {
  const [activeTab, setActiveTab] = useState<TabKey>("champion");
  const [showMore, setShowMore] = useState(false);

  const projections = useQuery({
    queryKey: ["projections"],
    queryFn: getTournamentProjections,
    enabled: activeTab !== "bracket",
    staleTime: 60_000,
  });

  if (activeTab !== "bracket" && projections.isLoading) return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>加载概率数据...</div>;
  if (activeTab !== "bracket" && (projections.isError || !projections.data)) return <div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>概率数据暂不可用</div>;

  const projectionData: TeamProjection[] = projections.data?.projections || [];

  // --- Tab 1: Champion Probability ---
  const renderChampionTab = () => {
    const sorted = [...projectionData].sort((a, b) => b.champion - a.champion);
    const displayList = showMore ? sorted : sorted.slice(0, 12);
    const rest = sorted.slice(12);

    // Group by tier
    const tierMap = new Map<ChampionTier, TeamProjection[]>();
    for (const t of displayList) {
      const tier = getChampionTier(t.champion);
      if (!tierMap.has(tier)) tierMap.set(tier, []);
      tierMap.get(tier)!.push(t);
    }
    const tierOrder: ChampionTier[] = ["hot", "contender", "darkhorse", "other"];

    const maxChampion = sorted.length > 0 ? sorted[0].champion : 1;

    return (
      <div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 20, lineHeight: 1.6 }}>
          以下概率基于蒙特卡洛模拟（Elo + Poisson 模型）生成。AI 预测结果会融入 Ensemble 模型影响最终概率。
        </div>

        {tierOrder.map((tier) => {
          const teams = tierMap.get(tier);
          if (!teams || teams.length === 0) return null;
          const cfg = TIER_CONFIG[tier];
          return (
            <div key={tier} className="champion-category">
              <h3 className={`champion-category__title ${cfg.cssClass}`}>
                {cfg.label}（{cfg.subtitle}）
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {teams.map(t => {
                  const barWidth = maxChampion > 0 ? (t.champion / maxChampion) * 100 : 0;
                  return (
                    <div key={t.team_id} style={{ display: "flex", alignItems: "center", gap: 12, padding: "8px 12px", background: "var(--card-bg)", borderRadius: 6, border: "1px solid var(--card-border)" }}>
                      <span style={{ minWidth: 90, fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>{getTeamZhName(t.team_id)}</span>
                      <div style={{ flex: 1, height: 8, background: "rgba(255,255,255,0.06)", borderRadius: 4, overflow: "hidden" }}>
                        <div style={{ width: `${barWidth}%`, height: "100%", background: cfg.color, borderRadius: 4, transition: "width 0.3s" }} />
                      </div>
                      <span style={{ minWidth: 52, textAlign: "right", fontSize: 13, fontWeight: 600, color: cfg.color }}>{fmtPct(t.champion)}</span>
                      <span style={{ minWidth: 80, fontSize: 11, color: "var(--text-secondary)" }}>
                        出线 {fmtPct(t.group_qualify)} · 4强 {fmtPct(t.semi_final)}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}

        {rest.length > 0 && (
          <button
            onClick={() => setShowMore(!showMore)}
            style={{ display: "block", width: "100%", padding: "10px 0", marginTop: 8, background: "transparent", border: "1px dashed var(--card-border)", borderRadius: 6, color: "var(--text-secondary)", fontSize: 13, cursor: "pointer", textAlign: "center" }}
          >
            {showMore ? "收起" : `显示更多（剩余 ${rest.length} 支球队）`}
          </button>
        )}
      </div>
    );
  };

  // --- Tab 2: Qualification Probability ---
  const renderQualificationTab = () => {
    const sorted = [...projectionData].sort((a, b) => b.group_qualify - a.group_qualify);
    const groupMap = new Map<string, TeamProjection[]>();
    for (const t of sorted) {
      const g = extractGroup(t.team_id);
      if (!groupMap.has(g)) groupMap.set(g, []);
      groupMap.get(g)!.push(t);
    }
    const groupKeys = Array.from(groupMap.keys()).sort();

    return (
      <div>
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 20, lineHeight: 1.6 }}>
          晋级概率包含小组前2名直接出线及最佳第3名出线两种路径。按小组分组展示。
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {groupKeys.map(gKey => {
            const teams = groupMap.get(gKey)!;
            return <GroupQualificationRow key={gKey} groupKey={gKey} teams={teams} />;
          })}
        </div>
      </div>
    );
  };

  // --- Tab 3: Bracket ---
  const renderBracketTab = () => {
    return (
      <div>
        <div style={{ color: "var(--risk-red)", fontSize: 13, fontWeight: 600, marginBottom: 16, padding: "10px 14px", border: "1px solid var(--risk-red)", borderRadius: 6, background: "rgba(255,107,107,0.08)", lineHeight: 1.6 }}>
          当前淘汰赛路径为简化模拟，真实 2026 赛制路径后续校准后再作为正式参考。
        </div>
        <BracketView />
      </div>
    );
  };

  return (
    <div>
      <div className="nav-tabs">
        {TABS.map(tab => (
          <button key={tab.key} className={activeTab === tab.key ? "active" : ""} onClick={() => setActiveTab(tab.key)}>
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === "champion" && renderChampionTab()}
      {activeTab === "qualification" && renderQualificationTab()}
      {activeTab === "bracket" && renderBracketTab()}
    </div>
  );
}

// --- GroupQualificationRow ---
function GroupQualificationRow({ groupKey, teams }: { groupKey: string; teams: TeamProjection[] }) {
  const [expanded, setExpanded] = useState(true);
  const sorted = [...teams].sort((a, b) => b.group_qualify - a.group_qualify);

  return (
    <SectionCard title={`${groupKey} 组`}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 80px 80px 80px", padding: "6px 0", fontSize: 11, color: "var(--text-secondary)", borderBottom: "1px solid var(--card-border)" }}>
        <span>球队</span>
        <span style={{ textAlign: "right" }}>晋级概率</span>
        <span style={{ textAlign: "right" }}>32强</span>
        <span style={{ textAlign: "right" }}>4强</span>
      </div>
      {sorted.map(t => (
        <div key={t.team_id} style={{ display: "grid", gridTemplateColumns: "1fr 80px 80px 80px", padding: "8px 0", fontSize: 13, borderBottom: "1px solid var(--card-border)", alignItems: "center" }}>
          <span style={{ fontWeight: 500, color: "var(--text-primary)" }}>{getTeamZhName(t.team_id)}</span>
          <span style={{ textAlign: "right", fontWeight: 600, color: t.group_qualify > 0.5 ? "var(--success-green)" : "var(--risk-red)" }}>{fmtPct(t.group_qualify)}</span>
          <span style={{ textAlign: "right", color: "var(--text-secondary)" }}>{fmtPct(t.round_of_32)}</span>
          <span style={{ textAlign: "right", color: "var(--text-secondary)" }}>{fmtPct(t.semi_final)}</span>
        </div>
      ))}
    </SectionCard>
  );
}
