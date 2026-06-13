import type { DataSource } from "../types";

export default function DataSources({ sources }: { sources: DataSource[] }) {
  return <section className="source-strip" aria-label="数据来源">
    {sources.map((source) => <a key={source.provider} href={source.source_url} target="_blank" rel="noreferrer">
      <span className={`status-dot ${source.status}`} />
      <div><b>{source.provider}</b><small>{new Date(source.fetched_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })}</small></div>
      <em>{Object.entries(source.coverage).map(([key, value]) => `${key} ${value}`).join(" · ")}</em>
    </a>)}
  </section>;
}

