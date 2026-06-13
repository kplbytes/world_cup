import type { Dashboard } from "../types";

export default function Header({ dashboard, refreshing, onRefresh }: { dashboard: Dashboard; refreshing: boolean; onRefresh: () => void }) {
  const updated = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", dateStyle: "medium", timeStyle: "short" }).format(new Date(dashboard.revision.created_at));
  return <header className="command-header">
    <div>
      <p className="eyebrow">2026 世界杯 / 本地分析台</p>
      <h1>小组赛预测指挥室</h1>
      <p className="lede">A–L 组完整赛程、积分状态与可解释概率模型</p>
    </div>
    <div className="revision-stamp">
      <span>版本 {String(dashboard.revision.id).padStart(3, "0")}</span>
      <strong>{dashboard.revision.model_version}</strong>
      <small>{updated} · {dashboard.revision.simulation_iterations.toLocaleString()} 次模拟</small>
      <button onClick={onRefresh} disabled={refreshing}>{refreshing ? "正在同步" : "同步赛果"}</button>
    </div>
  </header>;
}

