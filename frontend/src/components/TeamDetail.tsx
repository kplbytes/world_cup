import type { Group, Team } from "../types";
import { useQuery } from "@tanstack/react-query";
import { getTeamProfile } from "../api";
import ProbabilityBar from "./ProbabilityBar";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { formatChinaDate, isFinishedMatch } from "../utils/time";

export default function TeamDetail({ team, group, onClose }: { team: Team; group: Group; onClose: () => void }) {
  const fixtures = group.matches.filter((match) => [match.home_team.id, match.away_team.id].includes(team.id));
  const profileQuery = useQuery({ queryKey: ["team-profile", team.id], queryFn: () => getTeamProfile(team.id) });
  const profile = profileQuery.data?.profile;
  const teamName = getTeamDisplayFromRef(team);
  return <aside className="team-drawer" aria-label={`${teamName} 球队详情`}>
    <button className="app-button drawer-close" data-variant="warning" onClick={onClose}>关闭</button>
    <p className="eyebrow">球队档案 / {group.code} 组</p>
    <h2><i>{team.flag}</i>{teamName}</h2>
    <div className="team-metrics"><div><span>模型 ELO</span><b>{team.elo}</b></div><div><span>近期状态</span><b>{team.recent_form || "暂无"}</b></div><div><span>小组积分</span><b>{team.standing.points}</b></div><div><span>净胜球</span><b>{team.standing.goal_difference > 0 ? "+" : ""}{team.standing.goal_difference}</b></div></div>
    <section><h3>名次与晋级分布</h3><ProbabilityBar label="小组第一" value={team.qualification.first} /><ProbabilityBar label="小组第二" value={team.qualification.second} /><ProbabilityBar label="小组第三" value={team.qualification.third} tone="amber" /><ProbabilityBar label="总晋级" value={team.qualification.qualify} /></section>
    <section><h3>本组赛程</h3>{fixtures.map((match) => <div className="team-fixture" key={match.id}><span>{getTeamDisplayFromRef(match.home_team)} vs {getTeamDisplayFromRef(match.away_team)}</span><b>{isFinishedMatch(match) && match.home_score != null && match.away_score != null ? `${match.home_score}:${match.away_score}` : formatChinaDate(match.kickoff)}</b></div>)}</section>
    <section className="team-profile-panel"><h3>球队画像</h3>{profile ? <>
      <div className="trait-list">{profile.traits_json.length ? profile.traits_json.map((trait) => <span key={trait}>{trait}</span>) : <span>样本不足，暂无强标签</span>}</div>
      <div className="team-profile-metrics"><div><span>历史样本</span><b>{profile.sample_count}</b></div><div><span>世界杯样本</span><b>{profile.world_cup_sample_count}</b></div><div><span>平局倾向</span><b>{(profile.draw_rate_overall * 100).toFixed(0)}%</b></div><div><span>遇强韧性</span><b>{(profile.draw_resilience_score * 100).toFixed(0)}%</b></div><div><span>场均进球</span><b>{profile.goal_for_avg.toFixed(2)}</b></div><div><span>场均失球</span><b>{profile.goal_against_avg.toFixed(2)}</b></div></div>
      <p className="profile-summary">{profileQuery.data?.summary}</p>
      <small className="profile-source">数据来源：{profile.source_summary_json.mode}，仅用于功能验证，不代表真实历史表现。截止 {formatChinaDate(profile.profile_as_of)}（北京时间）</small>
    </> : <div className="detail-muted">{profileQuery.isLoading ? "画像加载中..." : "暂无球队画像"}</div>}</section>
    <p className="squad-unavailable">球员名单与实时身价：当前免费权威来源不可用，系统不会伪造。</p>
  </aside>;
}
