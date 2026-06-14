import { useQuery } from "@tanstack/react-query";
import { getTournamentProjections } from "../api";
import type { TeamProjection } from "../types";
import { getTeamDisplayName } from "../utils/teamNames";

const PROGRESSION_STAGES: { key: keyof TeamProjection; label: string }[] = [
  { key: "group_qualify", label: "出线" },
  { key: "round_of_32", label: "32强" },
  { key: "round_of_16", label: "16强" },
  { key: "quarter_final", label: "8强" },
  { key: "semi_final", label: "4强" },
  { key: "final", label: "决赛" },
  { key: "champion", label: "冠军" },
];

function fmtPct(value: number): string {
  if (value < 0.001) return "<0.1%";
  return (value * 100).toFixed(1) + "%";
}

export default function TournamentProjectionView() {
  const projections = useQuery({ queryKey: ["projections"], queryFn: getTournamentProjections });

  if (projections.isLoading) return <div className="empty">加载晋级概率...</div>;
  if (projections.isError || !projections.data) return <div className="empty">晋级概率暂不可用</div>;

  const data = projections.data.projections || [];

  // Group by tier
  const champions = data.filter((t: TeamProjection) => t.champion > 0.10).sort((a: TeamProjection, b: TeamProjection) => b.champion - a.champion);
  const contenders = data.filter((t: TeamProjection) => t.champion <= 0.10 && t.champion > 0.05).sort((a: TeamProjection, b: TeamProjection) => b.champion - a.champion);
  const darkHorses = data.filter((t: TeamProjection) => t.champion <= 0.05 && t.champion > 0.01).sort((a: TeamProjection, b: TeamProjection) => b.champion - a.champion);
  const others = data.filter((t: TeamProjection) => t.champion <= 0.01).sort((a: TeamProjection, b: TeamProjection) => b.group_qualify - a.group_qualify);

  return (
    <div className="projection-view">
      <h2>冠军概率</h2>
      <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 20, lineHeight: 1.6 }}>
        以下概率基于蒙特卡洛模拟（Elo + Poisson 模型）生成，综合考虑球队实力、赛程难度和晋级路径。AI 预测结果会融入 Ensemble 模型影响最终概率。
      </div>
      <div style={{ fontSize: 11, color: "var(--amber)", marginBottom: 16, padding: "6px 10px", border: "1px solid var(--amber)", borderRadius: "4px", background: "oklch(34% .025 80 / .1)", lineHeight: 1.5 }}>
        基于官方 32 强框架 + 简化第三名落位模拟。最佳第三名落位采用简化贪心分配，与 FIFA 官方组合表可能存在差异，冠军概率仅供参考。
      </div>

      {champions.length > 0 && (
        <div className="projection-section">
          <h3>争冠热门（&gt;10%）</h3>
          <div className="champion-cards">
            {champions.slice(0, 8).map((t: TeamProjection) => (
              <div key={t.team_id} className="champion-card">
                <div className="team-id">{getTeamDisplayName(t.team_id)}</div>
                <div className="champion-prob">{fmtPct(t.champion)}</div>
                <div className="prob-bar-container">
                  <div className="prob-bar" style={{ width: `${t.champion * 100}%`, background: "var(--mint)" }} />
                </div>
                <div className="sub-probs">
                  <span>出线 {fmtPct(t.group_qualify)}</span>
                  <span>4强 {fmtPct(t.semi_final)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {contenders.length > 0 && (
        <div className="projection-section">
          <h3>有力竞争者（5-10%）</h3>
          <div className="champion-cards">
            {contenders.slice(0, 8).map((t: TeamProjection) => (
              <div key={t.team_id} className="champion-card">
                <div className="team-id">{getTeamDisplayName(t.team_id)}</div>
                <div className="champion-prob" style={{ color: "var(--amber)" }}>{fmtPct(t.champion)}</div>
                <div className="prob-bar-container">
                  <div className="prob-bar" style={{ width: `${t.champion * 100 / 0.1 * 100}%`, background: "var(--amber)" }} />
                </div>
                <div className="sub-probs">
                  <span>出线 {fmtPct(t.group_qualify)}</span>
                  <span>4强 {fmtPct(t.semi_final)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {darkHorses.length > 0 && (
        <div className="projection-section">
          <h3>潜在黑马（1-5%）</h3>
          <div className="champion-cards">
            {darkHorses.slice(0, 8).map((t: TeamProjection) => (
              <div key={t.team_id} className="champion-card">
                <div className="team-id">{getTeamDisplayName(t.team_id)}</div>
                <div className="champion-prob" style={{ color: "var(--muted)" }}>{fmtPct(t.champion)}</div>
                <div className="prob-bar-container">
                  <div className="prob-bar" style={{ width: `${t.champion * 100 / 0.05 * 100}%`, background: "var(--muted)" }} />
                </div>
                <div className="sub-probs">
                  <span>出线 {fmtPct(t.group_qualify)}</span>
                  <span>4强 {fmtPct(t.semi_final)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {others.length > 0 && (
        <div className="projection-section">
          <h3>其他球队（&lt;1%）</h3>
          <div className="champion-cards">
            {others.slice(0, 8).map((t: TeamProjection) => (
              <div key={t.team_id} className="champion-card">
                <div className="team-id">{getTeamDisplayName(t.team_id)}</div>
                <div className="champion-prob" style={{ color: "var(--muted)" }}>{fmtPct(t.champion)}</div>
                <div className="prob-bar-container">
                  <div className="prob-bar" style={{ width: `${t.group_qualify * 100}%`, background: "var(--muted)" }} />
                </div>
                <div className="sub-probs">
                  <span>出线 {fmtPct(t.group_qualify)}</span>
                  <span>4强 {fmtPct(t.semi_final)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="projection-section">
        <h3>全部球队晋级概率</h3>
        <div className="table-wrap">
          <table className="data-table projection-table">
            <thead>
              <tr>
                <th>球队</th>
                {PROGRESSION_STAGES.map(s => <th key={s.key}>{s.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {data.map((t: TeamProjection) => (
                <tr key={t.team_id}>
                  <td className="version-cell">{getTeamDisplayName(t.team_id)}</td>
                  {PROGRESSION_STAGES.map(s => (
                    <td key={s.key} className={s.key === "champion" && t.champion > 0.05 ? "good" : ""}>
                      {fmtPct(t[s.key] as number)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
