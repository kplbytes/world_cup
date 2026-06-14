import { useState } from "react";
import type { Group } from "../types";
import MatchSummaryCard from "./MatchSummaryCard";
import { formatChinaTimeShort, isFinishedMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";

export default function AllMatches({ groups }: { groups: Group[] }) {
  const [filter, setFilter] = useState("all");
  const totalMatches = groups.reduce((sum, g) => sum + g.matches.length, 0);
  const matches = groups.flatMap((group) => group.matches).filter((match) => filter === "all" || match.status === filter);
  return <main className="all-matches">
    <section className="group-title"><div><p>赛事档案 / 共 {totalMatches} 场比赛</p><h2>全部比赛</h2></div><strong>{matches.length}<small>/ {totalMatches} 场当前筛选</small></strong></section>
    <div className="filter-row" aria-label="比赛状态筛选">
      {[ ["all", "全部"], ["scheduled", "未赛"], ["live", "进行中"], ["final", "终场"] ].map(([value, label]) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>)}
    </div>
    <div className="all-match-list">{matches.map((match) => {
      const homeZh = getTeamDisplayFromRef(match.home_team);
      const awayZh = getTeamDisplayFromRef(match.away_team);
      const timeStr = formatChinaTimeShort(match.kickoff);
      const statusLabel = isFinishedMatch(match) && match.home_score != null && match.away_score != null ? `比分 ${match.home_score}:${match.away_score}` : match.status === "live" ? "进行中" : match.prediction ? `预测 ${match.prediction.confidence_label}` : "待预测";
      return <div className="all-match-entry" key={match.id}>
        <b>{match.group_code}</b>
        <div className="all-match-info" style={{ padding: "10px 12px" }}>
          <div style={{ fontSize: 11, color: "var(--amber)", marginBottom: 4 }}>{match.group_code}组 | {timeStr}</div>
          <div style={{ fontWeight: 600, fontSize: 14, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{homeZh} VS {awayZh}</div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 2 }}>{statusLabel}{match.venue ? ` · ${match.venue}` : ""}</div>
        </div>
        <MatchSummaryCard match={match} />
      </div>;
    })}</div>
  </main>;
}
