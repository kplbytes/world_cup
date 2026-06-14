import type { DataSource } from "../types";
import { formatChinaTimeShort } from "../utils/time";

export default function DataSources({ sources }: { sources: DataSource[] }) {
  return <section className="source-strip" aria-label="数据来源">
    {sources.map((source) => {
      if (source.daily_limit !== undefined) {
        // Intelligence provider
        const isNoToken = source.status === "disabled_no_token";
        const statusText = isNoToken
          ? "未启用：未配置 Token"
          : source.status === "quota_limited"
            ? "配额受限：进入降级模式"
            : source.status === "available" || source.status === "ok"
              ? `已启用 · 今日请求 ${source.used_today} / ${source.daily_limit}`
              : `状态: ${source.status}`;
        return <div key={source.provider} className={`source-item ${source.status}`}>
          <div className="source-head">
            <span className={`status-dot ${isNoToken ? "disabled" : source.status}`} />
            <div>
              <b>{source.provider}</b>
              <small>{source.last_success_at ? formatChinaTimeShort(source.last_success_at) : "未请求"}</small>
            </div>
          </div>
          <em>{statusText}</em>
        </div>;
      }

      // Traditional DataSnapshot
      return <a key={source.provider} href={source.source_url} target="_blank" rel="noreferrer" className="source-item">
        <div className="source-head">
          <span className={`status-dot ${source.status}`} />
          <div>
            <b>{source.provider}</b>
            <small>{source.fetched_at ? formatChinaTimeShort(source.fetched_at) : "未更新"}</small>
          </div>
        </div>
        <em>{source.coverage ? Object.entries(source.coverage).map(([key, value]) => `${key} ${value}`).join(" · ") : "无覆盖信息"}</em>
      </a>;
    })}
  </section>;
}
