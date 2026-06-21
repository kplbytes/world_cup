import { useState, useMemo, useDeferredValue } from "react";
import type { Match, Group } from "../types";
import { isFinishedMatch, isSameChinaDate, isUpcomingMatch, isWithinNextHoursChina, isLiveMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import GroupDashboard from "./GroupDashboard";
import GroupNav from "./GroupNav";
import BracketView from "./BracketView";
import MatchSummaryCard from "./MatchSummaryCard";
import MatchDetailDrawer from "./MatchDetailDrawer";
import SectionCard from "./ui/SectionCard";
import EmptyState from "./ui/EmptyState";

interface MatchCenterProps {
  groups: Group[];
  onTeamSelect: (teamId: string) => void;
}

type TabKey = "future24" | "all" | "groups" | "knockout";

const TABS: { key: TabKey; label: string }[] = [
  { key: "future24", label: "未来 24 小时比赛" },
  { key: "all", label: "全部比赛" },
  { key: "groups", label: "分组赛" },
  { key: "knockout", label: "淘汰赛" },
];

type AllMatchFilter = "all" | "scheduled" | "live" | "final" | "today" | "locked" | "has_ai" | "high_risk";

const ALL_FILTERS: { key: AllMatchFilter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "scheduled", label: "未赛" },
  { key: "live", label: "进行中" },
  { key: "final", label: "已结束" },
  { key: "today", label: "今日" },
  { key: "locked", label: "已锁定" },
  { key: "has_ai", label: "有AI预测" },
  { key: "high_risk", label: "高风险" },
];

function isTodayChina(kickoff: string): boolean {
  return isSameChinaDate(kickoff);
}

function AllMatchesTab({
  groups,
  selectedMatch,
  onOpenDetails,
}: {
  groups: Group[];
  selectedMatch: Match | null;
  onOpenDetails: (match: Match) => void;
}) {
  const [searchText, setSearchText] = useState("");
  const deferredSearchText = useDeferredValue(searchText);
  const [filter, setFilter] = useState<AllMatchFilter>("all");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());

  const groupedMatches = useMemo(() => {
    const map = new Map<string, Match[]>();
    for (const g of groups) {
      map.set(g.code, g.matches);
    }
    return map;
  }, [groups]);

  const filteredGrouped = useMemo(() => {
    const result = new Map<string, Match[]>();
    for (const [code, matches] of groupedMatches) {
      const filtered = matches.filter((m) => {
        if (deferredSearchText) {
          const homeZh = getTeamDisplayFromRef(m.home_team);
          const awayZh = getTeamDisplayFromRef(m.away_team);
          const q = deferredSearchText.toLowerCase();
          if (!homeZh.toLowerCase().includes(q) && !awayZh.toLowerCase().includes(q) && !m.home_team.id.toLowerCase().includes(q) && !m.away_team.id.toLowerCase().includes(q)) return false;
        }
        switch (filter) {
          case "scheduled": return m.status === "scheduled";
          case "live": return isLiveMatch(m);
          case "final": return isFinishedMatch(m);
          case "today": return isTodayChina(m.kickoff);
          case "locked": return m.snapshot_status?.locked ?? false;
          case "has_ai": return m.ai_prediction != null;
          case "high_risk": return m.risk_flags && m.risk_flags.length > 0;
          default: return true;
        }
      });
      if (filtered.length > 0) result.set(code, filtered);
    }
    return result;
  }, [groupedMatches, deferredSearchText, filter]);

  const totalMatches = groups.flatMap((g) => g.matches).length;
  const filteredCount = Array.from(filteredGrouped.values()).reduce((sum, ms) => sum + ms.length, 0);

  const toggleGroup = (code: string) => {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(code)) next.delete(code);
      else next.add(code);
      return next;
    });
  };

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <input
          type="text"
          placeholder="搜索球队名称或代码..."
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          style={{
            width: "100%", padding: "8px 12px", border: "1px solid var(--card-border)",
            background: "var(--card-bg)", color: "var(--text-primary)", fontSize: "13px", borderRadius: "4px", outline: "none",
          }}
        />
      </div>

      <div className="filter-row" style={{ flexWrap: "wrap", marginBottom: 16 }}>
        {ALL_FILTERS.map(({ key, label }) => (
          <button key={key} className={filter === key ? "active" : ""} onClick={() => setFilter(key)}>{label}</button>
        ))}
      </div>

      <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 12 }}>
        共 {totalMatches} 场比赛，当前筛选 {filteredCount} 场
      </div>

      {Array.from(filteredGrouped.entries())
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([code, matches]) => {
          const isExpanded = expandedGroups.has(code);
          return (
            <div key={code} style={{ marginBottom: 4 }}>
              <button
                onClick={() => toggleGroup(code)}
                style={{
                  width: "100%", display: "flex", justifyContent: "space-between", alignItems: "center",
                  padding: "10px 14px", border: "1px solid var(--card-border)", background: "var(--card-bg)",
                  color: "var(--text-primary)", cursor: "pointer", fontSize: 13, fontWeight: 600, borderRadius: 4,
                }}
              >
                <span>
                  <span style={{ color: "var(--accent-yellow)", marginRight: 8 }}>{code}组</span>
                  {matches.length} 场比赛
                </span>
                <span style={{ color: "var(--text-secondary)", fontSize: 11 }}>{isExpanded ? "收起 ▲" : "展开 ▼"}</span>
              </button>
              {isExpanded && (
                <div style={{ border: "1px solid var(--card-border)", borderTop: 0, borderRadius: "0 0 4px 4px" }}>
                  <div className="today-match-grid" style={{ padding: 12, gap: 12 }}>
                    {matches.map((match) => (
                      <MatchSummaryCard
                        key={match.id}
                        match={match}
                        onOpenDetails={onOpenDetails}
                        detailsOpen={selectedMatch?.id === match.id}
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      {filteredGrouped.size === 0 && <EmptyState title="无匹配比赛" />}
    </div>
  );
}

export default function MatchCenter({ groups, onTeamSelect }: MatchCenterProps) {
  const [activeTab, setActiveTab] = useState<TabKey>("future24");
  const [selectedGroup, setSelectedGroup] = useState("A");
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);

  const currentGroup = groups.find((g) => g.code === selectedGroup) ?? groups[0];

  const future24hMatches = useMemo(() => {
    const now = new Date();
    return groups
      .flatMap((g) => g.matches)
      .filter((m) => isUpcomingMatch(m, now) && isWithinNextHoursChina(m.kickoff, 24, now))
      .sort((a: Match, b: Match) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
  }, [groups]);

  return (
    <div>
      <div className="nav-tabs">
        {TABS.map(({ key, label }) => (
          <button key={key} className={activeTab === key ? "active" : ""} onClick={() => setActiveTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {activeTab === "future24" && (
        <SectionCard title="未来 24 小时比赛（北京时间）" badge={`${future24hMatches.length} 场`}>
          {future24hMatches.length === 0 ? (
            <EmptyState title="未来 24 小时暂无比赛" />
          ) : (
            <div className="today-match-grid">
              {future24hMatches.map((match: Match) => (
                <MatchSummaryCard
                  key={match.id}
                  match={match}
                  onOpenDetails={setSelectedMatch}
                  detailsOpen={selectedMatch?.id === match.id}
                />
              ))}
            </div>
          )}
        </SectionCard>
      )}

      {activeTab === "all" && (
        <AllMatchesTab groups={groups} selectedMatch={selectedMatch} onOpenDetails={setSelectedMatch} />
      )}

      {activeTab === "groups" && currentGroup && (
        <div className="workspace">
          <GroupNav selected={currentGroup.code} onSelect={(code: string) => setSelectedGroup(code)} />
          <GroupDashboard group={currentGroup} onTeamSelect={onTeamSelect} onOpenDetails={setSelectedMatch} />
        </div>
      )}

      {activeTab === "knockout" && (
        <div>
          <div style={{ color: "var(--risk-red)", fontSize: 12, marginBottom: 16, padding: "10px 14px", border: "1px solid var(--risk-red)", borderRadius: 4, background: "rgba(255,107,107,0.08)", fontWeight: 600, lineHeight: 1.6 }}>
            当前淘汰赛路径为简化模拟，真实 2026 赛制路径后续校准后再作为正式参考。
          </div>
          <BracketView />
        </div>
      )}
      <MatchDetailDrawer open={selectedMatch != null} match={selectedMatch} onClose={() => setSelectedMatch(null)} />
    </div>
  );
}
