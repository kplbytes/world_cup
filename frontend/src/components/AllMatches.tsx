import { useState } from "react";
import type { Group } from "../types";
import MatchCard from "./MatchCard";

export default function AllMatches({ groups }: { groups: Group[] }) {
  const [filter, setFilter] = useState("all");
  const matches = groups.flatMap((group) => group.matches).filter((match) => filter === "all" || match.status === filter);
  return <main className="all-matches">
    <section className="group-title"><div><p>赛事档案 / 72 场比赛</p><h2>全部比赛</h2></div><strong>{matches.length}<small>/ 72 场当前筛选</small></strong></section>
    <div className="filter-row" aria-label="比赛状态筛选">
      {[ ["all", "全部"], ["scheduled", "未赛"], ["live", "进行中"], ["final", "终场"] ].map(([value, label]) => <button key={value} className={filter === value ? "active" : ""} onClick={() => setFilter(value)}>{label}</button>)}
    </div>
    <div className="all-match-list">{matches.map((match) => <div className="all-match-entry" key={match.id}><b>{match.group_code}</b><MatchCard match={match} /></div>)}</div>
  </main>;
}

