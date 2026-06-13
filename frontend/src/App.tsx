import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getDashboard, refreshDashboard } from "./api";
import DataSources from "./components/DataSources";
import AllMatches from "./components/AllMatches";
import DecisionView from "./components/DecisionView";
import GroupDashboard from "./components/GroupDashboard";
import GroupNav from "./components/GroupNav";
import Header from "./components/Header";
import TeamDetail from "./components/TeamDetail";
import "./styles.css";

export default function App() {
  const [selectedGroup, setSelectedGroup] = useState("A");
  const [view, setView] = useState<"group" | "all" | "decision">("group");
  const [selectedTeam, setSelectedTeam] = useState<string | null>(null);
  const client = useQueryClient();
  const dashboard = useQuery({ queryKey: ["dashboard"], queryFn: getDashboard });
  const refresh = useMutation({ mutationFn: refreshDashboard, onSuccess: () => client.invalidateQueries({ queryKey: ["dashboard"] }) });

  if (dashboard.isLoading) return <div className="state-screen"><span>正在加载赛事数据</span></div>;
  if (dashboard.isError || !dashboard.data) return <div className="state-screen error"><span>无法读取本地赛事数据</span><button onClick={() => dashboard.refetch()}>重试</button></div>;
  const group = dashboard.data.groups.find((item) => item.code === selectedGroup) ?? dashboard.data.groups[0];
  const team = group.teams.find((item) => item.id === selectedTeam) ?? null;
  return <div className="app-shell">
    <Header dashboard={dashboard.data} refreshing={refresh.isPending} onRefresh={() => refresh.mutate()} />
    <DataSources sources={dashboard.data.data_sources} />
    <div className="view-switch"><button className={view === "group" ? "active" : ""} onClick={() => setView("group")}>分组看板</button><button className={view === "all" ? "active" : ""} onClick={() => setView("all")}>全部比赛</button><button className={view === "decision" ? "active" : ""} onClick={() => setView("decision")}>决策视图</button></div>
    {view === "group" ? <div className="workspace"><GroupNav selected={group.code} onSelect={(code) => { setSelectedGroup(code); setSelectedTeam(null); }} /><GroupDashboard group={group} onTeamSelect={setSelectedTeam} /></div> : view === "all" ? <AllMatches groups={dashboard.data.groups} /> : <DecisionView />}
    {team ? <TeamDetail team={team} group={group} onClose={() => setSelectedTeam(null)} /> : null}
    <footer>预测仅供信息参考，不构成投注建议。足球比赛始终存在不可建模的偶然性。</footer>
  </div>;
}
