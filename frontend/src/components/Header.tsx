import type { Dashboard } from "../types";
import { formatChinaTimeShort } from "../utils/time";

export default function Header({ dashboard, refreshing, onRefresh }: { dashboard: Dashboard; refreshing: boolean; onRefresh: () => void }) {
  const updated = formatChinaTimeShort(dashboard.revision.created_at);
  return <header className="command-header">
    <div>
      <p className="eyebrow">2026 世界杯 / 本地分析台</p>
      <h1>2026 世界杯预测工作台</h1>
      <p className="lede">赛前预测、AI 辅助、赛后复盘与模型评分</p>
    </div>
    <div className="revision-stamp">
      <span>版本 {String(dashboard.revision.id).padStart(3, "0")}</span>
      <strong>{dashboard.revision.model_version}</strong>
      <small>{updated} · {dashboard.revision.simulation_iterations.toLocaleString()} 次模拟</small>
      <button className="app-button" data-variant="primary" onClick={onRefresh} disabled={refreshing}>
        {refreshing ? "正在同步" : "同步赛果"}
      </button>
    </div>
  </header>;
}
