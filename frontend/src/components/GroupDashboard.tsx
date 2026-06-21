import type { Group } from "../types";
import MatchSummaryCard from "./MatchSummaryCard";
import ProbabilityBar from "./ProbabilityBar";
import { getTeamDisplayName } from "../utils/teamNames";
import { isFinishedMatch } from "../utils/time";

export default function GroupDashboard({ group, onTeamSelect, onOpenDetails }: { group: Group; onTeamSelect: (teamId: string) => void; onOpenDetails?: (match: import("../types").Match) => void }) {
  return <main className="group-dashboard">
    <section className="group-title"><div><p>小组档案 / {group.code}</p><h2>{group.name}</h2></div><strong>{group.matches.filter(isFinishedMatch).length}<small>/ 6 场已结束</small></strong></section>
    <div className="dashboard-grid">
      <section className="standings-panel">
        <div className="section-heading"><h3>实时积分</h3><span>积分 / 净胜球 / 晋级</span></div>
        <div className="standings-table">
          {group.teams.map((team) => <button className="standing-row" key={team.id} aria-label={`查看 ${getTeamDisplayName(team.id)}`} onClick={() => onTeamSelect(team.id)}>
            <b className="position">{team.standing.position}</b><span className="team-name"><i>{team.flag}</i><strong>{getTeamDisplayName(team.id)}</strong><small>ELO {team.elo}</small></span>
            <span>{team.standing.played}<small>场</small></span><span>{team.standing.goal_difference > 0 ? "+" : ""}{team.standing.goal_difference}<small>净胜</small></span><b>{team.standing.points}<small>分</small></b>
          </button>)}
        </div>
        <div className="qualification-list"><h3>晋级概率</h3>{group.teams.map((team) => <ProbabilityBar key={team.id} label={getTeamDisplayName(team.id)} value={team.qualification.qualify} />)}</div>
      </section>
      <section className="matches-panel"><div className="section-heading"><h3>比赛档案</h3><span>点击查看模型细节</span></div><div className="today-match-grid">{group.matches.map((match) => <MatchSummaryCard key={match.id} match={match} onOpenDetails={onOpenDetails} />)}</div></section>
    </div>
  </main>;
}
