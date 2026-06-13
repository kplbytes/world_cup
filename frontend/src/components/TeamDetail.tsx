import type { Group, Team } from "../types";
import ProbabilityBar from "./ProbabilityBar";

export default function TeamDetail({ team, group, onClose }: { team: Team; group: Group; onClose: () => void }) {
  const fixtures = group.matches.filter((match) => [match.home_team.id, match.away_team.id].includes(team.id));
  return <aside className="team-drawer" aria-label={`${team.name} 球队详情`}>
    <button className="drawer-close" onClick={onClose}>关闭</button>
    <p className="eyebrow">球队档案 / {group.code} 组</p>
    <h2><i>{team.flag}</i>{team.name}</h2>
    <div className="team-metrics"><div><span>模型 ELO</span><b>{team.elo}</b></div><div><span>近期状态</span><b>{team.recent_form || "暂无"}</b></div><div><span>小组积分</span><b>{team.standing.points}</b></div><div><span>净胜球</span><b>{team.standing.goal_difference > 0 ? "+" : ""}{team.standing.goal_difference}</b></div></div>
    <section><h3>名次与晋级分布</h3><ProbabilityBar label="小组第一" value={team.qualification.first} /><ProbabilityBar label="小组第二" value={team.qualification.second} /><ProbabilityBar label="小组第三" value={team.qualification.third} tone="amber" /><ProbabilityBar label="总晋级" value={team.qualification.qualify} /></section>
    <section><h3>本组赛程</h3>{fixtures.map((match) => <div className="team-fixture" key={match.id}><span>{match.home_team.short_name} vs {match.away_team.short_name}</span><b>{match.status === "final" ? `${match.home_score}:${match.away_score}` : new Date(match.kickoff).toLocaleDateString("zh-CN", { timeZone: "Asia/Shanghai" })}</b></div>)}</section>
    <p className="squad-unavailable">球员名单与实时身价：当前免费权威来源不可用，系统不会伪造。</p>
  </aside>;
}

