import type { Group, Team, Match } from "../types";
import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { getTeamProfile } from "../api";
import ProbabilityBar from "./ProbabilityBar";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { formatChinaDate, isFinishedMatch } from "../utils/time";
import type { TeamProfile } from "../types";

function pct(value: number | null | undefined) {
  if (value == null) return "unavailable";
  return `${Math.round(value * 100)}%`;
}

function score(value: number | null | undefined) {
  if (value == null) return "unavailable";
  return value.toFixed(0);
}

function statsbombXgLabel(value: unknown) {
  if (!value || value === "unavailable" || typeof value !== "object") return "unavailable";
  const xg = value as { xg_for_avg?: number; xg_against_avg?: number; sample_count?: number };
  if (xg.xg_for_avg == null || xg.xg_against_avg == null) return "unavailable";
  return `${xg.xg_for_avg.toFixed(2)} / ${xg.xg_against_avg.toFixed(2)} · ${xg.sample_count ?? 0}场`;
}

function ProfileModule({ title, children }: { title: string; children: ReactNode }) {
  return <article className="profile-module"><h4>{title}</h4>{children}</article>;
}

function ProfileField({ label, value }: { label: string; value: React.ReactNode }) {
  return <div className="profile-field"><span>{label}</span><strong>{value}</strong></div>;
}

function TeamProfilePanel({ profile, summary }: { profile: TeamProfile; summary?: string }) {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const modules = profile.profile_modules_json as any;
  const longTerm = modules.long_term_strength || {};
  const recent = modules.recent_form || {};
  const attackDefense = modules.attack_defense || {};
  const lineup = modules.lineup_players || {};
  const environment = modules.environment || {};
  const quality = profile.team_profile_data_quality || {};
  return <>
    <div className="profile-scope">仅用于球队画像展示，不参与 Baseline / AI / Ensemble 预测计算</div>
    <div className="trait-list">{profile.traits_json.length ? profile.traits_json.map((trait) => <span key={trait}>{trait}</span>) : <span>样本不足，暂无强标签</span>}</div>
    <div className="profile-module-grid">
      <ProfileModule title="基础实力">
        <ProfileField label="长期评分" value={`${score(profile.long_term_strength_score)} / ${longTerm.grade ?? "unavailable"}`} />
        <ProfileField label="Elo" value={longTerm.elo ? Math.round(longTerm.elo) : "unavailable"} />
        <ProfileField label="近两年战绩" value={longTerm.two_year_record ? `${longTerm.two_year_record.wins}胜${longTerm.two_year_record.draws}平${longTerm.two_year_record.losses}负` : "unavailable"} />
        <ProfileField label="净胜球" value={longTerm.two_year_record?.goal_difference ?? "unavailable"} />
      </ProfileModule>
      <ProfileModule title="近期状态">
        <ProfileField label="状态分" value={score(profile.recent_form_score)} />
        <ProfileField label="近5场" value={recent.recent_5 ? `${recent.recent_5.wins}胜${recent.recent_5.draws}平${recent.recent_5.losses}负` : "unavailable"} />
        <ProfileField label="近5场进/失" value={recent.recent_5_goal_for_avg != null ? `${recent.recent_5_goal_for_avg.toFixed(2)} / ${recent.recent_5_goal_against_avg.toFixed(2)}` : "unavailable"} />
        <ProfileField label="连续不败" value={recent.unbeaten_streak ?? "unavailable"} />
      </ProfileModule>
      <ProfileModule title="攻防能力">
        <ProfileField label="进攻强度" value={`${score(profile.attack_score)} · ${attackDefense.attack_level ?? "unavailable"}`} />
        <ProfileField label="防守稳定" value={`${score(profile.defense_score)} · ${attackDefense.defense_level ?? "unavailable"}`} />
        <ProfileField label="节奏倾向" value={attackDefense.tempo_tendency ?? "unavailable"} />
        <ProfileField label="零封率" value={pct(profile.profile_modules_json?.attack_defense?.clean_sheet_rate)} />
        <ProfileField label="StatsBomb xG" value={statsbombXgLabel(attackDefense.xg)} />
      </ProfileModule>
      <ProfileModule title="战术风格">
        <div className="trait-list">{profile.tactical_style_tags.map((tag) => <span key={tag}>{tag}</span>)}</div>
      </ProfileModule>
      <ProfileModule title="阵容与球员风险">
        <ProfileField label="阵容完整度" value={score(profile.lineup_integrity_score)} />
        <ProfileField label="伤病风险" value={score(profile.injury_risk_score)} />
        <ProfileField label="官方名单" value={lineup.squad_size ? `${lineup.squad_size}人 · ${(lineup.average_caps ?? 0).toFixed(1)}场均出场` : "unavailable"} />
        <ProfileField label="位置深度" value={lineup.bench_depth?.depth_score != null ? `${lineup.bench_depth.depth_score.toFixed(0)} · GK ${lineup.bench_depth.position_counts?.GK ?? 0}/DF ${lineup.bench_depth.position_counts?.DF ?? 0}/MF ${lineup.bench_depth.position_counts?.MF ?? 0}/FW ${lineup.bench_depth.position_counts?.FW ?? 0}` : "unavailable"} />
        <ProfileField label="队内射手" value={lineup.top_scorers_in_squad?.length ? lineup.top_scorers_in_squad.slice(0, 2).map((player: { shirt_name?: string; goals?: number }) => `${player.shirt_name ?? "NA"} ${player.goals ?? 0}`).join("、") : "unavailable"} />
        <p>{lineup.note ?? "unavailable"}</p>
      </ProfileModule>
      <ProfileModule title="比赛环境适应">
        <ProfileField label="休息天数" value={profile.rest_days ?? "unavailable"} />
        <ProfileField label="疲劳分" value={score(profile.schedule_fatigue_score)} />
        <ProfileField label="环境适应" value={score(profile.environment_adaptation_score)} />
        <ProfileField label="旅行距离" value={environment.travel_distance_km != null ? `${Math.round(environment.travel_distance_km)} km` : "unavailable"} />
        <ProfileField label="时差变化" value={environment.timezone_shift_hours != null ? `${environment.timezone_shift_hours > 0 ? "+" : ""}${environment.timezone_shift_hours}h` : "unavailable"} />
        <p>{environment.status === "unavailable" ? "赛程、旅行、时差、气候和场地熟悉度 unavailable" : "已接入赛程、场地、旅行距离、时差和历史气候基线；实时天气仍 unavailable"}</p>
      </ProfileModule>
      <ProfileModule title="数据可信度">
        <ProfileField label="可信度" value={`${score(profile.data_quality_score)} · ${quality.quality_label ?? "unknown"}`} />
        <ProfileField label="是否 mock" value={quality.contains_mock ? "是" : "否"} />
        <ProfileField label="来源" value={(profile.source_list || []).join("、") || "unavailable"} />
        <ProfileField label="更新时间" value={formatChinaDate(profile.profile_as_of)} />
      </ProfileModule>
    </div>
    <p className="profile-summary">{summary}</p>
    <div className="profile-lists">
      <div><strong>优势</strong><p>{profile.strengths.length ? profile.strengths.join("、") : "暂无强优势"}</p></div>
      <div><strong>风险点</strong><p>{profile.risk_flags.length ? profile.risk_flags.join("、") : "暂无显著风险"}</p></div>
      <div><strong>缺失字段</strong><p>{profile.missing_fields.slice(0, 8).join("、") || "无"}</p></div>
    </div>
    <small className="profile-source">数据来源：{profile.source_summary_json?.mode ?? "未知"}。当前仅用于展示，不参与预测计算。</small>
  </>;
}

export default function TeamDetail({ team, group, allMatches, onClose }: { team: Team; group: Group; allMatches?: Match[]; onClose: () => void }) {
  const groupFixtures = group.matches.filter((match) => [match.home_team.id, match.away_team.id].includes(team.id));
  const knockoutFixtures = (allMatches ?? []).filter((match) => !group.matches.some((gm) => gm.id === match.id) && [match.home_team.id, match.away_team.id].includes(team.id));
  const fixtures = [...groupFixtures, ...knockoutFixtures];
  const profileQuery = useQuery({ queryKey: ["team-profile", team.id], queryFn: () => getTeamProfile(team.id) });
  const profile = profileQuery.data?.profile;
  const teamName = getTeamDisplayFromRef(team);
  return <aside className="team-drawer" aria-label={`${teamName} 球队详情`}>
    <button className="app-button drawer-close" data-variant="warning" onClick={onClose}>关闭</button>
    <p className="eyebrow">球队档案 / {group.code} 组</p>
    <h2><i>{team.flag}</i>{teamName}</h2>
    <div className="team-metrics"><div><span>模型 ELO</span><b>{team.elo}</b></div><div><span>近期状态</span><b>{team.recent_form || "暂无"}</b></div><div><span>小组积分</span><b>{team.standing.points}</b></div><div><span>净胜球</span><b>{team.standing.goal_difference > 0 ? "+" : ""}{team.standing.goal_difference}</b></div></div>
    <section><h3>名次与晋级分布</h3><ProbabilityBar label="小组第一" value={team.qualification.first} /><ProbabilityBar label="小组第二" value={team.qualification.second} /><ProbabilityBar label="小组第三" value={team.qualification.third} tone="amber" /><ProbabilityBar label="总晋级" value={team.qualification.qualify} /></section>
    <section><h3>赛程（含淘汰赛）</h3>{fixtures.map((match) => <div className="team-fixture" key={match.id}><span>{getTeamDisplayFromRef(match.home_team)} vs {getTeamDisplayFromRef(match.away_team)}</span><b>{isFinishedMatch(match) && match.home_score != null && match.away_score != null ? `${match.home_score}:${match.away_score}` : formatChinaDate(match.kickoff)}</b></div>)}</section>
    <section className="team-profile-panel"><h3>球队画像</h3>{profile ? <TeamProfilePanel profile={profile} summary={profileQuery.data?.summary} /> : <div className="detail-muted">{profileQuery.isLoading ? "画像加载中..." : "暂无球队画像"}</div>}</section>
    <p className="squad-unavailable">球员名单与实时身价：当前免费权威来源不可用，系统不会伪造。</p>
  </aside>;
}
