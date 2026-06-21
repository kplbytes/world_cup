import { useQuery } from "@tanstack/react-query";
import { getAIModels, getAIEvaluation } from "../api";
import { formatChinaTimeShort } from "../utils/time";
import { STATUS_CLASS, STATUS_LABELS, STATUS_ICON } from "../utils/constants";

export default function AIModelComparisonView() {
  const models = useQuery({ queryKey: ["ai-models"], queryFn: getAIModels });
  const evaluation = useQuery({ queryKey: ["ai-evaluation"], queryFn: getAIEvaluation });

  // Compute success/failure counts from evaluation data
  const aiByVersion = evaluation.data?.ai_by_version ?? {};
  const ensembleData = evaluation.data?.ensemble;

  return (
    <div className="ai-model-view">
      <h2>AI 模型对比</h2>

      {/* Model Status */}
      <div className="accuracy-section">
        <h3>模型状态</h3>
        {models.isLoading ? <div className="app-empty-state">加载中...</div> :
         models.isError ? <div className="app-empty-state error">模型数据加载失败: {models.error instanceof Error ? models.error.message : "未知错误"}</div> : (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>提供商</th>
                    <th>状态</th>
                    <th>角色</th>
                    <th>成本</th>
                    <th>延迟</th>
                    <th>提供商健康</th>
                    <th>最近成功</th>
                    <th>最近错误</th>
                  </tr>
                </thead>
                <tbody>
                  {(models.data?.models || []).map((m) => (
                    <tr key={m.model_version}>
                      <td className="version-cell">{m.display_name}</td>
                      <td>{m.provider}</td>
                      <td className={STATUS_CLASS[m.status] || ""}>
                        {STATUS_ICON[m.status] || ""} {STATUS_LABELS[m.status] || m.status}
                        {m.status === "disabled_no_key" && (
                          <span className="no-key-hint" style={{ display: "block", fontSize: "10px", color: "var(--coral)" }}>
                            需要配置 API 密钥
                          </span>
                        )}
                      </td>
                      <td>{m.role}</td>
                      <td>{m.cost_tier}</td>
                      <td>{m.latency_tier}</td>
                      <td>
                        {m.provider_health ? (
                          <span style={{ color: m.provider_health.available ? "var(--mint)" : "var(--coral)" }}>
                            {m.provider_health.available ? "可用" : "不可用"}
                            {m.provider_health.error && (
                              <span style={{ display: "block", fontSize: "10px", color: "var(--coral)" }}>{m.provider_health.error}</span>
                            )}
                          </span>
                        ) : "-"}
                      </td>
                      <td>{m.last_success_at ? formatChinaTimeShort(m.last_success_at) : "-"}</td>
                      <td>{m.last_error_at ? <span style={{ color: "var(--coral)", fontSize: "12px" }}>{formatChinaTimeShort(m.last_error_at)}</span> : "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {/* Model status summary */}
            <div style={{ display: "flex", gap: "16px", marginTop: "8px", fontSize: "12px", flexWrap: "wrap" }}>
              {(models.data?.models || []).map((m) => (
                <span key={m.model_version} style={{ padding: "2px 8px", borderRadius: "3px", background: m.status === "ready" ? "oklch(50% .1 150 / .15)" : "oklch(50% .1 25 / .1)" }}>
                  {STATUS_ICON[m.status]} {m.display_name}: {STATUS_LABELS[m.status] || m.status}
                </span>
              ))}
            </div>
          </>
        )}
        {!models.data?.enabled && (
          <div className="ai-disabled-note" style={{ color: "var(--amber)", fontSize: "12px", marginTop: "8px" }}>
            AI 预测当前未启用。设置 ENABLE_AI_PREDICTION=true 开启。
          </div>
        )}
      </div>

      {/* Evaluation Results */}
      <div className="accuracy-section">
        <h3>模型评分对比</h3>
        {evaluation.isLoading ? <div className="app-empty-state">加载中...</div> :
         evaluation.isError ? <div className="app-empty-state error">评分数据暂不可用: {evaluation.error instanceof Error ? evaluation.error.message : "未知错误"}</div> : (
          <>
            <div className="table-wrap">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th>样本</th>
                    <th>Brier</th>
                    <th>LogLoss</th>
                    <th>命中率</th>
                    <th>成功</th>
                    <th>失败</th>
                    <th>成功率</th>
                    <th>效果</th>
                  </tr>
                </thead>
                <tbody>
                  {/* System baseline */}
                  {evaluation.data?.system && (
                    <tr>
                      <td className="version-cell">系统基线 (Elo+Poisson)</td>
                      <td>{evaluation.data.system.sample_count}</td>
                      <td>{evaluation.data.system.brier?.toFixed(4) || "-"}</td>
                      <td>{evaluation.data.system.logloss?.toFixed(4) || "-"}</td>
                      <td>{evaluation.data.system.hit_rate != null ? `${(evaluation.data.system.hit_rate * 100).toFixed(1)}%` : "-"}</td>
                      <td>-</td><td>-</td><td>-</td><td>基线</td>
                    </tr>
                  )}
                  {/* AI models */}
                  {Object.entries(aiByVersion).map(([version, data]) => {
                    const effect = evaluation.data?.ai_effect?.[version];
                    const successRate = (data.helped + data.hurt) > 0 ? (data.helped / (data.helped + data.hurt) * 100).toFixed(1) + "%" : "-";
                    return (
                      <tr key={version}>
                        <td className="version-cell">{version}</td>
                        <td>{data.sample_count}</td>
                        <td>{data.brier?.toFixed(4) || "-"}</td>
                        <td>{data.logloss?.toFixed(4) || "-"}</td>
                        <td>{data.hit_rate != null ? `${(data.hit_rate * 100).toFixed(1)}%` : "-"}</td>
                        <td className="good">{data.helped}</td>
                        <td className="bad">{data.hurt}</td>
                        <td>{successRate}</td>
                        <td className={effect?.effect === "helped" ? "good" : effect?.effect === "hurt" ? "bad" : ""}>
                          {effect?.effect === "helped" ? "有帮助" : effect?.effect === "hurt" ? "有损害" : effect?.effect === "neutral" ? "中性" : "-"}
                        </td>
                      </tr>
                    );
                  })}
                  {/* Ensemble */}
                  {ensembleData && (
                    <tr>
                      <td className="version-cell">Ensemble</td>
                      <td>{ensembleData.sample_count}</td>
                      <td>{ensembleData.brier?.toFixed(4) || "-"}</td>
                      <td>{ensembleData.logloss?.toFixed(4) || "-"}</td>
                      <td>{ensembleData.hit_rate != null ? `${(ensembleData.hit_rate * 100).toFixed(1)}%` : "-"}</td>
                      <td className="good">{ensembleData.helped}</td>
                      <td className="bad">{ensembleData.hurt}</td>
                      <td>{(ensembleData.helped + ensembleData.hurt) > 0 ? (ensembleData.helped / (ensembleData.helped + ensembleData.hurt) * 100).toFixed(1) + "%" : "-"}</td>
                      <td className={evaluation.data?.ai_effect?.ensemble?.effect === "helped" ? "good" : evaluation.data?.ai_effect?.ensemble?.effect === "hurt" ? "bad" : ""}>
                        {evaluation.data?.ai_effect?.ensemble?.effect === "helped" ? "有帮助" : evaluation.data?.ai_effect?.ensemble?.effect === "hurt" ? "有损害" : "中性"}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* Average Brier/LogLoss summary */}
            {Object.keys(aiByVersion).length > 0 && (
              <div style={{ marginTop: "8px", fontSize: "12px", display: "flex", gap: "16px", flexWrap: "wrap" }}>
                {Object.entries(aiByVersion).map(([version, data]) => (
                  <span key={version} style={{ padding: "4px 8px", borderRadius: "3px", background: "oklch(34% .025 250 / .1)" }}>
                    {version}: Brier {data.brier?.toFixed(4) ?? "-"} / LogLoss {data.logloss?.toFixed(4) ?? "-"}
                  </span>
                ))}
                {ensembleData && (
                  <span style={{ padding: "4px 8px", borderRadius: "3px", background: "oklch(34% .025 150 / .1)" }}>
                    Ensemble: Brier {ensembleData.brier?.toFixed(4) ?? "-"} / LogLoss {ensembleData.logloss?.toFixed(4) ?? "-"}
                  </span>
                )}
              </div>
            )}

            {/* Parse error info */}
            {Object.entries(aiByVersion).some(([, data]) => data.hurt > 0) && (
              <div style={{ marginTop: "8px", fontSize: "12px", color: "var(--coral)" }}>
                ⚠️ 部分模型存在预测失败（损害场次 &gt; 0），可能包含解析错误或异常输出。建议检查各模型 error_code 分布。
              </div>
            )}

            {(!evaluation.data?.system?.sample_count || evaluation.data.system.sample_count < 5) && (
              <div className="sample-warning" style={{ color: "var(--amber)", fontSize: "12px", marginTop: "8px", padding: "6px 10px", border: "1px solid var(--amber)", borderRadius: "3px", background: "oklch(34% .025 80 / .1)" }}>
                样本不足，暂不能下结论。需要至少5场已结束比赛。
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
