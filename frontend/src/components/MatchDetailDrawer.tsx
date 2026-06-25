import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { Match, AIPredictionItem, AIModelStatus, EnsemblePredictionItem } from "../types";
import { getAIModels, getAIPredictions, getEnsemble, getMatchDetail, runAIPrediction, runEnsemble } from "../api";
import { formatChinaTimeShort, isFinishedMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { getMatchRecommendation, getSourceDisplayName, directionLabel, filterScorelinesByDirection } from "../utils/recommendation";

type Props = {
  open: boolean;
  match: Match | null;
  onClose: () => void;
};

type PredRow = {
  source: string;
  version: string;
  homeWin: number | null;
  draw: number | null;
  awayWin: number | null;
  recommendation: string;
  status: string;
  error: string | null;
  identicalToBaseline: boolean | null;
  deviationFromBaseline: number | null;
  isShadow?: boolean;
};

function profileScore(value: number | null | undefined): string {
  return value == null ? "unavailable" : value.toFixed(0);
}

function profileXg(value: unknown): string {
  if (!value || value === "unavailable" || typeof value !== "object") return "xG unavailable";
  const xg = value as { xg_for_avg?: number; xg_against_avg?: number; sample_count?: number };
  if (xg.xg_for_avg == null || xg.xg_against_avg == null) return "xG unavailable";
  return `xG ${xg.xg_for_avg.toFixed(2)}/${xg.xg_against_avg.toFixed(2)} · ${xg.sample_count ?? 0}场`;
}

function normalizeRecommendation(label: string | null | undefined, homeWin: number | null, draw: number | null, awayWin: number | null): string {
  if (label === "home_win" || label === "主胜") return "主胜";
  if (label === "draw" || label === "平局") return "平局";
  if (label === "away_win" || label === "客胜") return "客胜";
  if (homeWin == null || draw == null || awayWin == null) return "未生成";
  return directionLabel(homeWin, draw, awayWin);
}

function riskLabelClass(level: string | undefined): string {
  if (level === "低") return "low";
  if (level === "中") return "medium";
  return "high";
}

function deriveRisk(match: Match) {
  const pred = match.prediction;
  if (!pred) return { level: "中", reason: ["暂无系统预测"] };

  const reasons: string[] = [];
  const maxProb = Math.max(pred.home_win, pred.draw, pred.away_win);
  if (pred.draw >= 0.3) reasons.push("平局概率较高");
  if (maxProb < 0.45) reasons.push("双方实力接近");
  if ((pred.base_home_win != null && Math.abs(pred.base_home_win - pred.home_win) > 0.08)
    || (pred.base_draw != null && Math.abs(pred.base_draw - pred.draw) > 0.08)
    || (pred.base_away_win != null && Math.abs(pred.base_away_win - pred.away_win) > 0.08)) {
    reasons.push("系统内部调整差异");
  }
  if (!match.snapshot_status?.locked) reasons.push("当前无赛前决策快照，只作实时参考");
  if (reasons.length === 0) reasons.push("预测分布相对稳定");

  if (pred.confidence_label === "低") return { level: "高", reason: reasons };
  if (pred.confidence_label === "中") return { level: "中", reason: reasons };
  return { level: "低", reason: reasons };
}

function sectionTitle(label: string) {
  return <h4 style={{ margin: "0 0 10px", fontSize: 12, textTransform: "uppercase", letterSpacing: ".08em", color: "var(--amber)" }}>{label}</h4>;
}

export default function MatchDetailDrawer({ open, match, onClose }: Props) {
  const queryClient = useQueryClient();
  const [isMobileLayout, setIsMobileLayout] = useState(() => window.innerWidth <= 980);
  const aiModelsQuery = useQuery({
    queryKey: ["ai-models"],
    queryFn: getAIModels,
    staleTime: 60_000,
    enabled: open,
  });
  const aiQuery = useQuery({
    queryKey: ["ai-predictions", match?.id],
    queryFn: () => getAIPredictions(match!.id),
    enabled: open && Boolean(match?.id),
    staleTime: 60_000,
  });
  const ensembleQuery = useQuery({
    queryKey: ["ensemble", match?.id],
    queryFn: () => getEnsemble(match!.id),
    enabled: open && Boolean(match?.id),
    staleTime: 60_000,
  });
  const detailQuery = useQuery({
    queryKey: ["match-detail", match?.id],
    queryFn: () => getMatchDetail(match!.id),
    enabled: open && Boolean(match?.id),
  });

  const refreshAIMutation = useMutation({
    mutationFn: async () => {
      if (!match) throw new Error("未选择比赛");
      const runResp = await runAIPrediction(match.id, undefined, true);
      await runEnsemble(match.id);
      return runResp;
    },
    onSuccess: async () => {
      if (!match) return;
      await queryClient.invalidateQueries({ queryKey: ["ai-predictions", match.id] });
      await queryClient.invalidateQueries({ queryKey: ["ensemble", match.id] });
      // Invalidate dashboard so cards refresh after AI/Ensemble update
      await queryClient.invalidateQueries({ queryKey: ["dashboard"] });
      await Promise.all([
        queryClient.refetchQueries({ queryKey: ["ai-predictions", match.id] }),
        queryClient.refetchQueries({ queryKey: ["ensemble", match.id] }),
      ]);
    },
  });

  useEffect(() => {
    if (!open) return;
    document.body.style.overflow = 'hidden';
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onResize = () => setIsMobileLayout(window.innerWidth <= 980);
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("resize", onResize);
    onResize();
    return () => {
      document.body.style.overflow = '';
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("resize", onResize);
    };
  }, [open, onClose]);

  const modelMap = useMemo(() => {
    const map = new Map<string, AIModelStatus>();
    for (const model of aiModelsQuery.data?.models ?? []) map.set(model.model_version, model);
    return map;
  }, [aiModelsQuery.data]);

  const aiPredictions = aiQuery.data?.predictions ?? [];
  const ensemblePredictions = ensembleQuery.data?.predictions ?? [];
  const hasAIPredictionError = aiQuery.isError || ensembleQuery.isError;
  const aiErrorMessage = aiQuery.error instanceof Error ? aiQuery.error.message : null;
  const ensembleErrorMessage = ensembleQuery.error instanceof Error ? ensembleQuery.error.message : null;

  // Memoized: match detail + recommendation + risk
  const { detailMatch, profiles, ensemble, rec, risk, scorelineList, isFinished, lockedText, scoringText, scoringReason } = useMemo(() => {
    if (!match) return { detailMatch: null, profiles: null, ensemble: null, rec: null, risk: null, scorelineList: [], isFinished: false, lockedText: "", scoringText: "", scoringReason: "" };
    const detailMatch = detailQuery.data ?? match;
    const profiles = detailMatch?.team_profiles ?? null;
    const ensemble = ensemblePredictions[0] ?? null;
    const rec = getMatchRecommendation(match!, aiPredictions, ensemble);
    const risk = deriveRisk(match!);
    const scorelineList = match!.prediction?.scorelines?.length ? filterScorelinesByDirection(match!.prediction.scorelines, rec).slice(0, 3) : [];
    const isFinished = isFinishedMatch(match!);
    const lockedText = match!.snapshot_status?.locked ? "赛前决策快照" : match!.snapshot_status?.is_fallback ? "fallback 快照" : "无赛前快照";
    const scoringText = match!.snapshot_status?.participates_in_model_score ? "是" : "否";
    const scoringReason = match!.snapshot_status?.participates_in_model_score
      ? "暂无"
      : !isFinished
        ? "比赛未结束"
        : match!.snapshot_status?.real_time_only
          ? "开赛后生成预测"
          : match!.snapshot_status?.locked || match!.snapshot_status?.is_fallback
            ? "暂无"
            : "无赛前预测快照";
    return { detailMatch, profiles, ensemble, rec, risk, scorelineList, isFinished, lockedText, scoringText, scoringReason };
  }, [detailQuery.data, match, ensemblePredictions, aiPredictions]);

  // Memoized: prediction table rows
  const { baselineRow, aiRows, ensembleRow, ensembleWeights, ensembleSourceStatus } = useMemo(() => {
    if (!match) return { baselineRow: null, aiRows: [], ensembleRow: null, ensembleWeights: null, ensembleSourceStatus: null };
    const m = match;
    const baselineRow: PredRow = {
      source: "Baseline",
      version: m.prediction?.model_version ?? "baseline",
      homeWin: m.prediction?.base_home_win ?? m.prediction?.home_win ?? null,
      draw: m.prediction?.base_draw ?? m.prediction?.draw ?? null,
      awayWin: m.prediction?.base_away_win ?? m.prediction?.away_win ?? null,
      recommendation: normalizeRecommendation(null, m.prediction?.base_home_win ?? m.prediction?.home_win ?? null, m.prediction?.base_draw ?? m.prediction?.draw ?? null, m.prediction?.base_away_win ?? m.prediction?.away_win ?? null),
      status: m.prediction ? "已生成" : "未生成",
      error: null,
      identicalToBaseline: null,
      deviationFromBaseline: null,
    };

    const availableVersions = [...new Set(aiPredictions.map((p: AIPredictionItem) => p.model_version))];
    const enabledVersions = [...modelMap.keys()].filter(v => v.startsWith("ai-"));
    const allAIVersions = [...new Set([...availableVersions, ...enabledVersions])];

    const aiRows: PredRow[] = allAIVersions.map((version) => {
      const pred = aiPredictions.find((p: AIPredictionItem) => p.model_version === version);
      const model = modelMap.get(version);
      const label = model?.display_name ?? version.replace(/^ai-/, "").replace(/-v\d+$/, "");
      if (!pred) {
        return {
          source: label, version, homeWin: null, draw: null, awayWin: null,
          recommendation: "未生成", status: model?.status === "disabled_no_key" ? "未配置 API Key" : "未生成",
          error: model?.status === "disabled_no_key" ? "API Key 未配置" : null,
          identicalToBaseline: null, deviationFromBaseline: null,
          isShadow: model?.prompt_version === "worldcup-ai-v2" || version.includes("-v2"),
        };
      }
      if (pred.error_message || pred.error_code) {
        return {
          source: label, version, homeWin: null, draw: null, awayWin: null,
          recommendation: "未生成", status: "AI 预测失败",
          error: pred.error_message || pred.error_code || "未知错误",
          identicalToBaseline: null, deviationFromBaseline: null,
          isShadow: pred.prompt_version === "worldcup-ai-v2" || version.includes("-v2"),
        };
      }
      if (pred.parsed_home_win == null || pred.parsed_draw == null || pred.parsed_away_win == null) {
        return {
          source: label, version, homeWin: null, draw: null, awayWin: null,
          recommendation: "解析失败", status: "解析失败", error: "解析失败",
          identicalToBaseline: null, deviationFromBaseline: null,
          isShadow: pred.prompt_version === "worldcup-ai-v2" || version.includes("-v2"),
        };
      }
      return {
        source: label, version,
        homeWin: pred.parsed_home_win, draw: pred.parsed_draw, awayWin: pred.parsed_away_win,
        recommendation: normalizeRecommendation(pred.recommended_label, pred.parsed_home_win, pred.parsed_draw, pred.parsed_away_win),
        status: "已生成", error: null,
        identicalToBaseline: pred.identical_to_baseline ?? null,
        deviationFromBaseline: pred.deviation_from_baseline ?? null,
        isShadow: pred.prompt_version === "worldcup-ai-v2" || version.includes("-v2"),
      };
    });

    const ensembleRow: PredRow = ensemble
      ? {
          source: "Ensemble", version: ensemble.model_version,
          homeWin: ensemble.home_win, draw: ensemble.draw, awayWin: ensemble.away_win,
          recommendation: normalizeRecommendation(null, ensemble.home_win, ensemble.draw, ensemble.away_win),
          status: "已生成", error: null, identicalToBaseline: null, deviationFromBaseline: null,
        }
      : {
          source: "Ensemble", version: "ensemble-v1",
          homeWin: null, draw: null, awayWin: null,
          recommendation: "未生成", status: "未生成",
          error: aiPredictions.length === 0 ? "AI 未生成 / API Key 未配置" : "市场缺失或尚未生成集成预测",
          identicalToBaseline: null, deviationFromBaseline: null,
        };

    const ensembleWeights = ensemble
      ? [
          `系统权重 ${(ensemble.system_weight * 100).toFixed(0)}%`,
          `市场权重 ${(ensemble.market_weight * 100).toFixed(0)}%`,
          ...Object.entries(ensemble.ai_weights).map(([k, v]) => `${k.replace("ai-", "")} ${(Number(v) * 100).toFixed(0)}%`),
        ]
      : [];

    const ensembleSourceStatus = ensemble?.source_status as {
      system?: boolean;
      market?: boolean;
      ai_versions?: string[];
    } | undefined;

    return { baselineRow, aiRows, ensembleRow, ensembleWeights, ensembleSourceStatus };
  }, [match, aiPredictions, modelMap, ensemble]);

  if (!open || !match || !rec || !risk || !baselineRow || !ensembleRow || !ensembleWeights) return null;

  const teamHome = getTeamDisplayFromRef(match.home_team);
  const teamAway = getTeamDisplayFromRef(match.away_team);
  const titleLine = `${teamHome} vs ${teamAway}`;
  const statusLine = isFinished ? "已结束" : match.status === "live" ? "进行中" : "未赛";

  const refreshLabel = refreshAIMutation.isPending ? "AI 预测中..." : "刷新本场 AI 预测";

  return (
    <>
      <div className="match-detail-backdrop" onClick={onClose} />
      <aside
        className={isMobileLayout ? "match-detail-modal" : "match-detail-drawer"}
        role="dialog"
        aria-modal="true"
        aria-label="比赛详情"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="match-detail-header">
          <div>
            <div className="match-detail-eyebrow">{match.group_code}组 · {statusLine}</div>
            <h2>{titleLine}</h2>
            <div className="match-detail-meta">
              <span>{formatChinaTimeShort(match.kickoff)}</span>
              <span>{match.venue ?? "场地待定"}</span>
              <span>{match.snapshot_status?.locked_at ? `快照 ${formatChinaTimeShort(match.snapshot_status.locked_at)}` : "无赛前快照"}</span>
            </div>
            <div className="match-detail-meta match-detail-meta-secondary">
              <span>当前预测口径：{match.snapshot_status?.real_time_only ? "实时展示" : "赛前决策快照"}</span>
              <span>快照状态：{lockedText}</span>
              <span>是否参与赛后评分：{scoringText}</span>
            </div>
          </div>
          <button className="app-button drawer-close" data-variant="warning" onClick={onClose}>关闭</button>
        </div>

        <section className="detail-section">
          {sectionTitle("综合结论")}
          <div className="detail-hero">
            <div className="detail-hero-item">
              <span>综合推荐</span>
              <strong>{rec.valid ? rec.label : "待生成"}</strong>
            </div>
            <div className="detail-hero-item">
              <span>推荐来源</span>
              <strong>{rec.valid ? getSourceDisplayName(rec.source) : "无"}</strong>
            </div>
            <div className="detail-hero-item">
              <span>预测比分</span>
              <strong>{match.prediction ? `${match.prediction.home_xg.toFixed(1)} : ${match.prediction.away_xg.toFixed(1)}` : "待生成"}</strong>
            </div>
            <div className="detail-hero-item">
              <span>风险等级</span>
              <strong className={riskLabelClass(risk.level)}>{risk.level}风险</strong>
            </div>
            <div className="detail-hero-item">
              <span>置信度</span>
              <strong>{match.prediction?.confidence_label ?? "暂无"}</strong>
            </div>
          </div>
          {!rec.valid && (
            <div className="detail-muted">
              原因：{ensembleRow.error ?? "AI 未生成 / 市场缺失 / API Key 未配置"}
            </div>
          )}
          {isFinished && rec.valid && (
            <div className="detail-muted">
              {match.snapshot_status?.locked ? "已完赛 / 赛前预测已生成" : match.snapshot_status?.real_time_only ? "已完赛 / 实时预测" : "已完赛"}
            </div>
          )}
          {isFinished && !rec.valid && !match.snapshot_status?.participates_in_model_score && (
            <div className="detail-muted">
              缺少赛前预测快照，不参与评分
            </div>
          )}
        </section>

        <section className="detail-section">
          {sectionTitle("三方概率对比")}
          <div className="prob-compare-table">
            <div className="prob-row head">
              <span>来源</span><span>主胜</span><span>平</span><span>客胜</span><span>推荐</span>
            </div>
            {[baselineRow, ...aiRows, ensembleRow].map((row) => (
              <div className="prob-row" key={`${row.source}-${row.version}`}>
                <span className="source-name">
                  {row.source}{row.isShadow ? " (Shadow)" : ""}
                  {row.identicalToBaseline === true && (
                    <span style={{ fontSize: 10, color: "var(--coral, #e06060)", marginLeft: 4 }} title="AI 预测与 Baseline 几乎一致，可能未提供独立判断">⚠</span>
                  )}
                </span>
                <span>{row.homeWin == null ? "未生成" : `${(row.homeWin * 100).toFixed(1)}%`}</span>
                <span>{row.draw == null ? "未生成" : `${(row.draw * 100).toFixed(1)}%`}</span>
                <span>{row.awayWin == null ? "未生成" : `${(row.awayWin * 100).toFixed(1)}%`}</span>
                <span>{row.isShadow && row.status === "已生成" ? "独立观察" : row.recommendation}</span>
              </div>
            ))}
          </div>
          {ensemble && (
            <>
              <div className="detail-muted">
                Ensemble 权重：{ensembleWeights.join(" · ")}
              </div>
              <div className="detail-muted">
                参与来源：Baseline {ensembleSourceStatus?.system ? "已参与" : "缺失"}；AI {ensembleSourceStatus?.ai_versions?.length ?? 0} 个模型参与；Market {ensembleSourceStatus?.market ? "已参与" : "缺失，权重已自动重分配"}；Team Profile 未参与当前 Ensemble（独立影子模型）。
              </div>
            </>
          )}
          {aiRows.some(r => r.identicalToBaseline === true) && (
            <div className="detail-muted" style={{ color: "var(--coral, #e06060)" }}>
              ⚠ 部分 AI 预测与 Baseline 概率偏差 &lt;1%，可能未提供独立判断。建议关注 deviation 值或尝试 force 刷新。
            </div>
          )}
        </section>

        {match.market && (
        <section className="detail-section">
          {sectionTitle("市场赔率")}
          <div className="detail-grid-2">
            <div><span>主胜</span><strong>{(match.market.home_probability * 100).toFixed(1)}%</strong></div>
            <div><span>平局</span><strong>{(match.market.draw_probability * 100).toFixed(1)}%</strong></div>
            <div><span>客胜</span><strong>{(match.market.away_probability * 100).toFixed(1)}%</strong></div>
            <div><span>隐含返还率</span><strong>{match.market.raw_overround != null ? `${(match.market.raw_overround * 100).toFixed(1)}%` : "—"}</strong></div>
          </div>
          {match.market.divergence && (
            <div className="detail-muted">
              市场分歧：{match.market.divergence.level}（最大偏差 {(match.market.divergence.max_divergence * 100).toFixed(1)}%）
            </div>
          )}
          <div className="detail-muted">
            数据来源：{match.source ?? "未知"}
            {match.source_updated_at && ` · 获取时间：${formatChinaTimeShort(match.source_updated_at)}`}
          </div>
        </section>
        )}

        <section className="detail-section">
          {sectionTitle("比分与 xG")}
          <div className="detail-grid-2">
            <div><span>预测比分</span><strong>{match.prediction ? `${match.prediction.home_xg.toFixed(1)} : ${match.prediction.away_xg.toFixed(1)}` : "待生成"}</strong></div>
            <div><span>xG</span><strong>{match.prediction ? `主队 ${match.prediction.home_xg.toFixed(2)} / 客队 ${match.prediction.away_xg.toFixed(2)}` : "待生成"}</strong></div>
          </div>
          <div className="scoreline-chip-list">
            {scorelineList.length > 0 ? scorelineList.map((s) => (
              <span key={`${s.home_goals}-${s.away_goals}`}>{s.home_goals}:{s.away_goals}</span>
            )) : <span className="detail-muted">暂无比分分布数据</span>}
          </div>
        </section>

        <section className="detail-section">
          {sectionTitle("风险解释")}
          <div className={`risk-badge ${riskLabelClass(risk.level)}`}>{risk.level}风险</div>
          <ul className="detail-list">
            {risk.reason.map((item) => <li key={item}>{item}</li>)}
          </ul>
          {!match.snapshot_status?.locked && <div className="detail-muted">当前无赛前决策快照，只作实时参考。</div>}
        </section>

        <section className="detail-section profile-section">
          {sectionTitle("球队画像")}
          <div className="detail-muted">
            当前球队画像仅用于展示，不参与 Baseline / AI / Ensemble 预测计算。
          </div>
          <div className="profile-versus-grid">
            {[{ side: "主队", team: teamHome, item: profiles?.home }, { side: "客队", team: teamAway, item: profiles?.away }].map(({ side, team, item }) => (
              <article className="profile-team-card" key={side}>
                <div className="profile-team-title"><span>{side}</span><strong>{team}</strong></div>
                {item ? <>
                  {(() => {
                    const modules = item.profile.profile_modules_json || {};
                    const lineup = modules.lineup_players || {};
                    const environment = modules.environment || {};
                    const attackDefense = modules.attack_defense || {};
                    const quality = item.profile.team_profile_data_quality || {};
                    const moduleRows = [
                      ["基础实力", profileScore(item.profile.long_term_strength_score)],
                      ["近期状态", profileScore(item.profile.recent_form_score)],
                      ["攻防能力", `${profileScore(item.profile.attack_score)} / ${profileScore(item.profile.defense_score)} · ${profileXg(attackDefense.xg)}`],
                      ["战术风格", item.profile.tactical_style_tags.length ? item.profile.tactical_style_tags.slice(0, 2).join("、") : "unavailable"],
                      ["阵容与球员风险", lineup.squad_size ? `${lineup.status || "official_squad_available"} · ${lineup.squad_size}人` : lineup.status || "unavailable"],
                      ["比赛环境适应", `${profileScore(item.profile.environment_adaptation_score)} · ${environment.status || "unavailable"}`],
                      ["数据可信度", `${profileScore(item.profile.data_quality_score)} · ${quality.quality_label ?? "unknown"}`],
                    ];
                    return <div className="profile-module-list">
                      {moduleRows.map(([label, value]) => <div className="profile-field" key={label}><span>{label}</span><strong>{value}</strong></div>)}
                    </div>;
                  })()}
                  <div className="trait-list">{item.profile.traits_json.length ? item.profile.traits_json.map((trait) => <span key={trait}>{trait}</span>) : <span>样本不足，暂无强标签</span>}</div>
                  <p>{item.summary}</p>
                  <small>{item.profile.sample_count} 场样本 · {(item.profile.source_list || []).join("、") || item.profile.source_summary_json?.mode || "未知"} · 仅展示，不参与预测</small>
                </> : <div className="detail-muted">画像尚未构建</div>}
              </article>
            ))}
          </div>
        </section>

        <section className="detail-section">
          {sectionTitle("赛前决策快照")}
          <div className="detail-grid-2">
            <div><span>当前预测口径</span><strong>{match.snapshot_status?.real_time_only ? "实时展示" : "赛前决策快照"}</strong></div>
            <div><span>快照时间</span><strong>{match.snapshot_status?.locked_at ? `${formatChinaTimeShort(match.snapshot_status.locked_at)} 北京时间` : "无"}</strong></div>
            <div><span>距离开赛</span><strong>{match.snapshot_status?.locked_at && match.kickoff ? (() => {
              const hours = (new Date(match.kickoff).getTime() - new Date(match.snapshot_status.locked_at!).getTime()) / (1000 * 60 * 60);
              return hours > 0 ? `${hours.toFixed(1)} 小时` : "开赛后生成";
            })() : "—"}</strong></div>
            <div><span>是否参与赛后评分</span><strong>{scoringText}</strong></div>
            {match.snapshot_status?.real_time_only && (
              <div><span>不参与评分原因</span><strong>开赛后生成</strong></div>
            )}
            {!match.snapshot_status?.real_time_only && !match.snapshot_status?.participates_in_model_score && scoringReason !== "暂无" && (
              <div><span>不参与评分原因</span><strong>{scoringReason}</strong></div>
            )}
          </div>
        </section>

        {isFinished && match.match_review && (
        <section className="detail-section">
          {sectionTitle("赛后复盘")}
          {(() => {
            const review = match.match_review!;
            const resultLabel: Record<string, string> = { home: "主胜", draw: "平局", away: "客胜" };
            const sourceLabel: Record<string, string> = { baseline: "Baseline", ai: "AI", ensemble: "Ensemble", market: "市场" };
            return (
              <>
                <div className="detail-grid-2">
                  <div><span>实际赛果</span><strong>{resultLabel[review.actual_result] ?? review.actual_result} ({review.actual_score.home}:{review.actual_score.away})</strong></div>
                  <div><span>方向命中</span><strong style={{ color: review.winner_hit ? "var(--success-green)" : review.winner_hit === false ? "var(--risk-red)" : "var(--text-secondary)" }}>{review.winner_hit == null ? "无预测" : review.winner_hit ? "命中" : "偏差"}</strong></div>
                  <div><span>最佳模型</span><strong>{review.best_model ? (sourceLabel[review.best_model] ?? review.best_model) : "无"}</strong></div>
                </div>
                <div className="prob-compare-table" style={{ marginTop: 8 }}>
                  <div className="prob-row head">
                    <span>来源</span><span>预测方向</span><span>命中</span><span>Brier</span><span>实际概率</span>
                  </div>
                  {(["baseline", "ai", "ensemble"] as const).map((src) => {
                    const r = review[src];
                    if (!r) return (
                      <div className="prob-row" key={src}>
                        <span className="source-name">{sourceLabel[src]}</span>
                        <span>暂无</span><span>—</span><span>—</span><span>—</span>
                      </div>
                    );
                    return (
                      <div className="prob-row" key={src}>
                        <span className="source-name">{sourceLabel[src]}</span>
                        <span>{resultLabel[r.predicted_result] ?? r.predicted_result}</span>
                        <span style={{ color: r.outcome_hit ? "var(--success-green)" : "var(--risk-red)" }}>{r.outcome_hit ? "命中" : "偏差"}</span>
                        <span>{r.brier.toFixed(4)}</span>
                        <span>{(r.actual_probability * 100).toFixed(1)}%</span>
                      </div>
                    );
                  })}
                </div>
                {/* P0-4: Result sync metadata */}
                {(match.result_source || match.result_synced_at) && (
                  <div className="detail-muted" style={{ marginTop: 8 }}>
                    数据来源：{match.result_source ?? "未知"}
                    {match.result_synced_at && ` · 同步时间：${formatChinaTimeShort(match.result_synced_at)}`}
                    {match.revision_id != null && ` · Revision #${match.revision_id}`}
                  </div>
                )}
              </>
            );
          })()}
        </section>
        )}

        <section className="detail-section">
          {sectionTitle("AI / Ensemble")}
          <div className="drawer-actions">
            <button className="app-button drawer-primary-btn" data-variant="primary" onClick={() => refreshAIMutation.mutate()} disabled={refreshAIMutation.isPending}>
              {refreshLabel}
            </button>
          </div>
          {refreshAIMutation.isError && (
            <div className="detail-error">
              AI 预测失败：{refreshAIMutation.error instanceof Error ? refreshAIMutation.error.message : "未知错误"}
            </div>
          )}
          {hasAIPredictionError && (
            <div className="detail-error">
              {aiErrorMessage && <div>AI 预测请求失败：{aiErrorMessage}</div>}
              {ensembleErrorMessage && <div>Ensemble 请求失败：{ensembleErrorMessage}</div>}
            </div>
          )}
          {aiQuery.isFetching && <div className="detail-muted">加载 AI 预测中...</div>}
          {aiPredictions.length > 0 ? (
            <div className="drawer-ai-list">
              {aiPredictions.map((p) => (
                <div className="drawer-ai-card" key={p.id}>
                  <strong>{p.model_version}</strong>
                  <span>{p.provider}</span>
                  <span>{p.error_message || p.error_code ? `错误：${p.error_message || p.error_code}` : `主胜 ${(p.parsed_home_win ?? 0) * 100}% / 平 ${(p.parsed_draw ?? 0) * 100}% / 客胜 ${(p.parsed_away_win ?? 0) * 100}%`}</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="detail-muted">本场尚未生成 AI 预测。</div>
          )}
          {ensemble ? (
            <div className="drawer-ensemble">
              <strong>Ensemble ({ensemble.model_version})</strong>
              <span>主胜 {(ensemble.home_win * 100).toFixed(1)}% / 平 {(ensemble.draw * 100).toFixed(1)}% / 客胜 {(ensemble.away_win * 100).toFixed(1)}%</span>
              <span>{ensemble.reason}</span>
            </div>
          ) : (
            <div className="detail-muted">{ensembleRow.error ?? "暂无 Ensemble 数据。"}</div>
          )}
        </section>
      </aside>
    </>
  );
}
