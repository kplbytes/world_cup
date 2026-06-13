import type { Group } from "../types";
import MatchCard from "./MatchCard";
import ProbabilityBar from "./ProbabilityBar";

export default function GroupDashboard({ group, onTeamSelect }: { group: Group; onTeamSelect: (teamId: string) => void }) {
  return <main className="group-dashboard">
    <section className="group-title"><div><p>小组档案 / {group.code}</p><h2>{group.name}</h2></div><strong>{group.matches.filter((match) => match.status === "final").length}<small>/ 6 场已结束</small></strong></section>
    <div className="dashboard-grid">
      <section className="standings-panel">
        <div className="section-heading"><h3>实时积分</h3><span>积分 / 净胜球 / 晋级</span></div>
        <div className="standings-table">
          {group.teams.map((team) => <button className="standing-row" key={team.id} aria-label={`查看 ${team.name}`} onClick={() => onTeamSelect(team.id)}>
            <b className="position">{team.standing.position}</b><span className="team-name"><i>{team.flag}</i><strong>{team.short_name}</strong><small>ELO {team.elo}</small></span>
            <span>{team.standing.played}<small>场</small></span><span>{team.standing.goal_difference > 0 ? "+" : ""}{team.standing.goal_difference}<small>净胜</small></span><b>{team.standing.points}<small>分</small></b>
          </button>)}
        </div>
        <div className="qualification-list"><h3>晋级概率</h3>{group.teams.map((team) => <ProbabilityBar key={team.id} label={team.short_name} value={team.qualification.qualify} />)}</div>
      </section>
      <section className="matches-panel"><div className="section-heading"><h3>比赛档案</h3><span>点击展开模型细节</span></div>{group.matches.map((match) => <MatchCard key={match.id} match={match} />)}</section>
    </div>
  </main>;
}
