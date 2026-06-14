import { Suspense, lazy, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getDashboard, refreshDashboard } from "./api";
import TeamDetail from "./components/TeamDetail";
import DailyDashboard from "./components/DailyDashboard";
import MatchCenter from "./components/MatchCenter";
import AppHeader from "./components/ui/AppHeader";
import PageShell from "./components/ui/PageShell";
import "./styles.css";

const ModelReviewCenter = lazy(() => import("./components/ModelReviewCenter"));
const TournamentCenter = lazy(() => import("./components/TournamentCenter"));

type ViewType = "daily" | "matches" | "models" | "tournament";

const NAV_ITEMS: { key: ViewType; label: string }[] = [
  { key: "daily", label: "今日工作台" },
  { key: "matches", label: "比赛中心" },
  { key: "models", label: "模型复盘" },
  { key: "tournament", label: "冠军与赛程" },
];

export default function App() {
  const [selectedGroup, setSelectedGroup] = useState("A");
  const [view, setView] = useState<ViewType>("daily");
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null);
  const client = useQueryClient();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: getDashboard });
  const refresh = useMutation({ mutationFn: refreshDashboard, onSuccess: () => client.invalidateQueries({ queryKey: ["dashboard"] }) });

  if (dashboard.isLoading) return <div className="state-screen"><span>正在加载赛事数据</span></div>;
  if (dashboard.isError || !dashboard.data) return <div className="state-screen error"><span>无法读取本地赛事数据</span><button onClick={() => dashboard.refetch()}>重试</button></div>;
  const group = dashboard.data.groups.find((item) => item.code === selectedGroup) ?? dashboard.data.groups[0];
  const team = group.teams.find((item) => item.id === selectedTeam) ?? null;

  const isHome = view === "daily";

  return (
    <div className="app-shell">
      <AppHeader
        mode={isHome ? "home" : "compact"}
        brand="2026 世界杯预测工作台"
        subtitle="赛前预测 · AI 辅助 · 赛后复盘"
        version={String(dashboard.data.revision.id).padStart(3, "0")}
        modelVersion={dashboard.data.revision.model_version}
        onSync={() => refresh.mutate()}
        syncing={refresh.isPending}
        nav={
          !isHome ? (
            <div className="nav-tabs">
              {NAV_ITEMS.map((item) => (
                <button
                  key={item.key}
                  className={view === item.key ? "active" : ""}
                  onClick={() => setView(item.key)}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ) : undefined
        }
      />

      {isHome && (
        <div className="nav-tabs" style={{ marginTop: 0 }}>
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              className={view === item.key ? "active" : ""}
              onClick={() => setView(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      )}

      {dashboard.data.revision.model_version === "elo-poisson-v1-intel-numeric" && (
        <div style={{ background: "rgba(246,195,67,0.1)", border: "1px solid var(--accent-yellow)", color: "var(--accent-yellow)", padding: "6px 14px", borderRadius: "4px", fontSize: "12px", marginBottom: "10px" }}>
          当前正在使用实验性数值修正版本，该版本预测数据仅供验证，并非投注建议。
        </div>
      )}

      <PageShell wide={view === "matches" || view === "tournament"}>
        <Suspense fallback={<div style={{ color: "var(--text-secondary)", padding: 24, textAlign: "center" }}>加载页面中...</div>}>
          {view === "daily" ? <DailyDashboard />
           : view === "matches" ? <MatchCenter groups={dashboard.data.groups} onTeamSelect={setSelectedTeam} />
           : view === "models" ? <ModelReviewCenter />
           : <TournamentCenter />
          }
        </Suspense>
      </PageShell>

      {team ? <TeamDetail team={team} group={group} onClose={() => setSelectedTeam(null)} /> : null}
      <footer>预测仅供信息参考，不构成投注建议。足球比赛始终存在不可建模的偶然性。</footer>
    </div>
  );
}
