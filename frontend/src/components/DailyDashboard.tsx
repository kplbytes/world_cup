import React, { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getWorkflowStatus,
  triggerDailyOpen,
  triggerPreMatch,
  triggerFullWorkflow,
  getWorkflowRuns,
  getDashboard,
  getAccuracyCommandCenter,
  getDecisionSnapshotStatus,
} from "../api";
import type { WorkflowStatus, Match, ButtonState, DecisionSnapshotStatus } from "../types";
import { formatChinaTimeShort, isFinishedMatch, isUpcomingMatch, isWithinNextHoursChina } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { directionLabel } from "../utils/recommendation";
import ActionButton from "./ActionButton";
import MatchSummaryCard from "./MatchSummaryCard";
import MatchDetailDrawer from "./MatchDetailDrawer";
import StatusStrip from "./ui/StatusStrip";
import type { StatusItem } from "./ui/StatusStrip";
import SectionCard from "./ui/SectionCard";
import MetricCard from "./ui/MetricCard";
import EmptyState from "./ui/EmptyState";

const AUTO_DAILY_OPEN_PARAMS = {
  with_ai: true,
  with_ensemble: true,
  auto_lock: true,
  only_missing: true,
  limit: 10,
  hours: 48,
  since_hours: 24,
} as const;

// ── Helpers ──────────────────────────────────────────────────────────

function statusDot(color: string) {
  return (
    <span
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: color,
        marginRight: 6,
        verticalAlign: "middle",
      }}
    />
  );
}

function CollapsibleSection({ title, badge, defaultOpen = false, children }: {
  title: string;
  badge?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <SectionCard
      title={title}
      badge={badge}
      action={
        <button
          onClick={() => setOpen(!open)}
          style={{
            background: "transparent",
            border: "1px solid var(--card-border)",
            color: "var(--text-secondary)",
            padding: "4px 10px",
            borderRadius: 4,
            cursor: "pointer",
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {open ? "收起" : "展开"}
        </button>
      }
    >
      {open ? children : null}
    </SectionCard>
  );
}

// ── Main Component ───────────────────────────────────────────────────

export default function DailyDashboard() {
  const queryClient = useQueryClient();
  const [autoTriggered, setAutoTriggered] = useState(false);
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);

  // Data queries
  const statusQuery = useQuery({
    queryKey: ["workflow-status"],
    queryFn: getWorkflowStatus,
    staleTime: 30_000,
  });

  const dashboardQuery = useQuery({
    queryKey: ["dashboard"],
    queryFn: getDashboard,
    staleTime: 30_000,
  });

  const accQuery = useQuery({
    queryKey: ["accuracy-command-center"],
    queryFn: getAccuracyCommandCenter,
    staleTime: 60_000,
  });

  const runsQuery = useQuery({
    queryKey: ["workflow-runs"],
    queryFn: () => getWorkflowRuns(5),
    staleTime: 30_000,
  });

  const snapshotQuery = useQuery({
    queryKey: ["decision-snapshot-status"],
    queryFn: getDecisionSnapshotStatus,
    staleTime: 30_000,
  });

  const status = statusQuery.data as WorkflowStatus | undefined;
  const snapshotStatus = snapshotQuery.data as DecisionSnapshotStatus | undefined;
  const btnStates = status?.button_states;

  // Mutations
  const dailyOpenMutation = useMutation({
    mutationFn: triggerDailyOpen,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const preMatchMutation = useMutation({
    mutationFn: triggerPreMatch,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const fullMutation = useMutation({
    mutationFn: triggerFullWorkflow,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
      queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });

  const anyRunning =
    dailyOpenMutation.isPending ||
    preMatchMutation.isPending ||
    fullMutation.isPending;

  useEffect(() => {
    if (autoTriggered) return;
    if (statusQuery.data?.recommended_action !== "run_daily_open_workflow") return;
    setAutoTriggered(true);
    dailyOpenMutation.mutate(AUTO_DAILY_OPEN_PARAMS);
  }, [autoTriggered, dailyOpenMutation, statusQuery.data?.recommended_action]);

  const future24hMatches = useMemo(() => {
    const dashboard = dashboardQuery.data;
    if (!dashboard) return [];
    const now = new Date();
    const allMatches: Match[] = [];
    for (const group of dashboard.groups) {
      for (const match of group.matches) {
        if (isUpcomingMatch(match, now) && isWithinNextHoursChina(match.kickoff, 24, now)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
    return allMatches;
  }, [dashboardQuery.data]);

  const future48hMatches = useMemo(() => {
    const dashboard = dashboardQuery.data;
    if (!dashboard) return [];
    const now = new Date();
    const allMatches: Match[] = [];
    for (const group of dashboard.groups) {
      for (const match of group.matches) {
        if (isUpcomingMatch(match, now) && isWithinNextHoursChina(match.kickoff, 48, now)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
    return allMatches;
  }, [dashboardQuery.data]);

  const finishedMatches = useMemo(() => {
    const dashboard = dashboardQuery.data;
    if (!dashboard) return [];
    const allMatches: Match[] = [];
    for (const group of dashboard.groups) {
      for (const match of group.matches) {
        if (isFinishedMatch(match)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(b.kickoff).getTime() - new Date(a.kickoff).getTime());
    return allMatches;
  }, [dashboardQuery.data]);

  // Key matches (3-5 most noteworthy)
  const keyMatches = useMemo(() => {
    if (future24hMatches.length === 0) return [];
    const scored = future24hMatches.map((m) => {
      let score = 0;
      const pred = m.prediction;
      const market = m.market;
      if (pred?.base_home_win != null) {
        const baselineRec = directionLabel(pred.base_home_win, pred.base_draw!, pred.base_away_win!);
        const currentRec = directionLabel(pred.home_win, pred.draw, pred.away_win);
        if (baselineRec !== currentRec) score += 3;
      }
      if (market?.divergence?.level === "高") score += 2;
      else if (market?.divergence?.level === "中") score += 1;
      if (pred?.confidence_label === "低") score += 2;
      else if (pred?.confidence_label === "中") score += 1;
      if (pred && pred.draw > 0.3) score += 1;
      if (pred && pred.draw > 0.35) score += 1;
      const hoursToKickoff = (new Date(m.kickoff).getTime() - Date.now()) / (1000 * 60 * 60);
      if (hoursToKickoff > 0 && hoursToKickoff < 2) score += 2;
      else if (hoursToKickoff > 0 && hoursToKickoff < 6) score += 1;
      return { match: m, score };
    });
    scored.sort((a, b) => b.score - a.score);
    return scored.slice(0, Math.min(5, Math.max(3, future24hMatches.length))).map((s) => s.match);
  }, [future24hMatches]);

  // Workflow runs
  const runs =
    (runsQuery.data as { runs?: import("../types").WorkflowRunInfo[] } | undefined)
      ?.runs ?? [];

  // Button states
  const dailyOpenBtn: ButtonState = btnStates?.daily_open ?? { enabled: true, reason: "" };
  const aiBtn: ButtonState = btnStates?.ai_prediction ?? { enabled: true, reason: "" };
  const fullBtn: ButtonState = btnStates?.full ?? { enabled: true, reason: "" };

  // ── Build status strip items ──
  const statusItems: StatusItem[] = useMemo(() => {
    if (!status) return [];
    const rawStatus = status.today_status;
    let todayLabel: string;
    let todayTone: StatusItem["tone"];
    if (rawStatus === "already_run" || rawStatus === "completed") {
      todayLabel = "已更新"; todayTone = "ok";
    } else if (rawStatus === "running") {
      todayLabel = "运行中"; todayTone = "warn";
    } else if (rawStatus === "partial_success") {
      todayLabel = "部分完成"; todayTone = "warn";
    } else if (rawStatus === "failed") {
      todayLabel = "失败"; todayTone = "error";
    } else {
      todayLabel = "未运行"; todayTone = "error";
    }

    const ensembleReady = (status.upcoming_matches?.ensemble_ready ?? 0) > 0;

    const snapReady = snapshotStatus?.snapshots_ready ?? 0;
    const snapTotal = snapshotStatus?.matches_total ?? 0;

    const items: StatusItem[] = [
      { label: "今日状态", value: todayLabel, tone: todayTone },
    ];

    // AI status with coverage breakdown
    const aiStatus = status.ai_status;
    if (aiStatus) {
      const aiConfigured = aiStatus.configured_models ?? 0;
      const aiEffective = aiStatus.effective_for_ensemble ?? 0;
      const aiFailed = aiStatus.failed ?? 0;
      const aiParseError = aiStatus.parse_error ?? 0;
      const aiAttempted = aiStatus.attempted ?? 0;

      if (aiConfigured > 0) {
        // Count today's matches needing AI
        const totalToday = future24hMatches.length;
        const covered = Math.min(status.upcoming_matches?.ai_ready ?? 0, totalToday);
        const missing = Math.max(0, totalToday - covered);

        const aiTone: StatusItem["tone"] = aiEffective > 0 && missing === 0 ? "ok" : aiFailed > 0 ? "error" : missing > 0 ? "warn" : "neutral";
        let aiValue: string;
        if (missing > 0 && aiFailed > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场，${missing} 场缺失；成功 ${aiStatus.success}，失败 ${aiFailed}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (missing > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场，${missing} 场缺失；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiFailed > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，失败 ${aiFailed}，参与 Ensemble ${aiEffective}`;
        } else if (aiParseError > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiEffective > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiAttempted > 0) {
          aiValue = `${aiAttempted} 场已调用，暂无有效结果`;
        } else {
          aiValue = "未运行";
        }
        items.push({ label: "AI", value: aiValue, tone: aiTone });
      } else {
        items.push({ label: "AI", value: "未配置", tone: "neutral" });
      }
    } else {
      // Fallback to old ai_stats
      const aiCalls = status.ai_stats?.today_ai_calls ?? 0;
      items.push({ label: "AI", value: aiCalls > 0 ? `已运行 ${aiCalls}` : "未运行", tone: aiCalls > 0 ? "ok" : "neutral" });
    }

    items.push({ label: "Ensemble", value: ensembleReady ? "已生成" : "未生成", tone: ensembleReady ? "ok" : "neutral" });

    if (snapTotal > 0) {
      items.push({ label: "赛前决策快照", value: `${snapReady}/${snapTotal}`, tone: snapReady === snapTotal ? "ok" : "warn" });
      const missing = snapTotal - snapReady;
      if (missing > 0) {
        items.push({ label: "缺失", value: `${missing}场`, tone: "error" });
      }
    }

    return items;
  }, [status, snapshotStatus, future24hMatches.length]);

  // ── Next step suggestion ──
  const nextStep = useMemo<{ text: string; tone: "ok" | "warn" | "error" } | null>(() => {
    if (!status) return null;

    // Use backend next_action if available
    if (status.next_action && status.next_action.action !== "none") {
      const actionTone: "ok" | "warn" | "error" =
        status.next_action.action === "wait" ? "warn" :
        status.next_action.action === "run_daily_open_workflow" ? "error" :
        status.next_action.action === "run_ai_prediction" ? "warn" : "warn";
      return { text: status.next_action.message, tone: actionTone };
    }

    const rawStatus = status.today_status;
    const needsAi = status.upcoming_matches?.needs_ai ?? 0;
    const aiReady = status.upcoming_matches?.ai_ready ?? 0;
    const ensembleReady = status.upcoming_matches?.ensemble_ready ?? 0;

    if (rawStatus === "not_run" || rawStatus === "needs_run") {
      return { text: '建议：先点击"更新今日数据"，同步昨晚赛果和今日赛程。', tone: "error" };
    }
    if (rawStatus === "failed") {
      return { text: "今日流程失败，请查看工作流日志，优先重试失败步骤。", tone: "error" };
    }
    if (rawStatus === "partial_success") {
      return { text: "今日流程部分失败，请查看工作流日志，并优先重试失败步骤。", tone: "warn" };
    }
    if (needsAi > 0) {
      return { text: `今日有 ${needsAi} 场比赛尚未生成 AI 预测，可点击"运行 AI 预测"。`, tone: "warn" };
    }
    if (aiReady > 0 && ensembleReady === 0) {
      return { text: "AI 预测已完成，可生成 Ensemble 综合预测。", tone: "warn" };
    }
    if (rawStatus === "already_run" || rawStatus === "completed") {
      return { text: "今日预测已准备完成。赛后将使用开赛前最后一份有效预测进行复盘。", tone: "ok" };
    }
    return null;
  }, [status]);

  // ── Model performance summary ──
  const modelSummary = useMemo(() => {
    const d = accQuery.data as import("../types").AccuracyCommandCenter | undefined;
    if (!d) return null;
    const minSample = d.version_scores?.length ? Math.min(...d.version_scores.map((v) => v.sample_count)) : 0;
    const insufficient = minSample < 5;
    const currentDefault = d.model_recommendation?.recommended_model_version ?? "baseline";
    const aiEval = d.ai_evaluation;
    const aiScoreStatus = aiEval?.ensemble
      ? aiEval.ensemble.helped > aiEval.ensemble.hurt ? "有帮助" : aiEval.ensemble.helped === aiEval.ensemble.hurt ? "中性" : "有损害"
      : "暂无数据";
    return { minSample, insufficient, currentDefault, aiScoreStatus };
  }, [accQuery.data]);

  return (
    <div>
      {/* A. Status Summary Strip */}
      <SectionCard title="今日状态" badge={status ? formatChinaTimeShort(status.last_run_at ?? new Date().toISOString()) : "加载中"}>
        <StatusStrip items={statusItems} />
      </SectionCard>

      {/* B. Next Step Suggestion */}
      {nextStep && (
        <div className={`next-step${nextStep.tone === "ok" ? " next-step--ok" : nextStep.tone === "error" ? " next-step--error" : ""}`} style={{ marginBottom: 20 }}>
          {nextStep.text}
        </div>
      )}

      {/* C. Today's Key Matches */}
      {keyMatches.length > 0 && (
        <SectionCard title="今日重点关注" badge={`${keyMatches.length} 场`}>
          <div className="key-match-highlight">
            {keyMatches.map((m, i) => {
              const home = getTeamDisplayFromRef(m.home_team);
              const away = getTeamDisplayFromRef(m.away_team);
              const pred = m.prediction;
              const reasons: string[] = [];
              if (pred?.base_home_win != null) {
                const baselineRec = directionLabel(pred.base_home_win, pred.base_draw!, pred.base_away_win!);
                const currentRec = directionLabel(pred.home_win, pred.draw, pred.away_win);
                if (baselineRec !== currentRec) reasons.push("AI 与 baseline 分歧较大");
              }
              if (m.market?.divergence?.level === "高") reasons.push("市场分歧高");
              if (pred?.confidence_label === "低") reasons.push("低置信度");
              if (pred && pred.draw > 0.3) reasons.push("平局概率高");
              const hoursToKickoff = (new Date(m.kickoff).getTime() - Date.now()) / (1000 * 60 * 60);
              if (hoursToKickoff > 0 && hoursToKickoff < 2) reasons.push("即将开赛");
              const reasonText = reasons.length > 0 ? reasons.join("、") : "值得关注";
              return (
                <div key={m.id} style={{ fontSize: 13, marginBottom: i < keyMatches.length - 1 ? 6 : 0, lineHeight: 1.5 }}>
                  <span style={{ fontWeight: 600 }}>{i + 1}.</span>{" "}
                  <span style={{ fontWeight: 600 }}>{home} vs {away}</span>
                  ：{reasonText}
                </div>
              );
            })}
          </div>
        </SectionCard>
      )}

      {/* D. Today's Matches */}
      <SectionCard title="未来 24 小时比赛（北京时间）" badge={`${future24hMatches.length} 场`}>
        {future24hMatches.length === 0 ? (
          <EmptyState title="未来 24 小时暂无比赛" />
        ) : (
          <div className="today-match-grid">
            {future24hMatches.map((m) => (
              <MatchSummaryCard
                key={m.id}
                match={m}
                onOpenDetails={setSelectedMatch}
                detailsOpen={selectedMatch?.id === m.id}
              />
            ))}
          </div>
        )}
      </SectionCard>

      {/* D2. Next 48h Matches */}
      {future48hMatches.length > 0 && (
        <CollapsibleSection title="未来 48 小时比赛（北京时间）" badge={`${future48hMatches.length} 场`} defaultOpen={false}>
          <div className="today-match-grid">
            {future48hMatches.map((m) => (
              <MatchSummaryCard
                key={m.id}
                match={m}
                onOpenDetails={setSelectedMatch}
                detailsOpen={selectedMatch?.id === m.id}
              />
            ))}
          </div>
        </CollapsibleSection>
      )}

      {/* E. Last Night's Review */}
      <SectionCard title="已结束比赛复盘" badge={`${finishedMatches.length} 场`}>
        {finishedMatches.length === 0 ? (
          <EmptyState>暂无已结束比赛。</EmptyState>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {finishedMatches.map((m) => {
              const home = getTeamDisplayFromRef(m.home_team);
              const away = getTeamDisplayFromRef(m.away_team);
              const score = m.home_score != null && m.away_score != null ? `${m.home_score} : ${m.away_score}` : "进行中";
              const pred = m.prediction;
              const hasPreMatchSnapshot = m.snapshot_status?.locked ?? false;
              const scoringText = m.snapshot_status?.participates_in_model_score
                ? "已纳入评分"
                : m.snapshot_status?.real_time_only
                  ? "未纳入：仅有赛后实时预测"
                  : "未纳入：无赛前快照";
              let baselineHit: string | null = null;
              let errorType = "";
              if (pred && m.home_score != null && m.away_score != null) {
                const actual = m.home_score > m.away_score ? "home" : m.home_score < m.away_score ? "away" : "draw";
                const baselinePred = directionLabel(pred.base_home_win ?? pred.home_win, pred.base_draw ?? pred.draw, pred.base_away_win ?? pred.away_win);
                const baselineMap: Record<string, string> = { 主胜: "home", 平局: "draw", 客胜: "away" };
                baselineHit = baselineMap[baselinePred] === actual ? "命中" : "偏差";
                if (baselineHit === "偏差") {
                  if (actual === "draw") errorType = "平局漏判";
                  else if (baselinePred === "主胜" && actual === "away") errorType = "方向反转";
                  else errorType = "冷门偏差";
                }
              }
              return (
                <div key={m.id} className="yesterday-row">
                  <span style={{ fontWeight: 600, minWidth: 160 }}>{home} vs {away}</span>
                  <span style={{ fontWeight: 700, minWidth: 60 }}>{score}</span>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                    赛前预测：{hasPreMatchSnapshot ? "有" : "无"}
                  </span>
                  <span style={{ fontSize: 11, color: m.snapshot_status?.participates_in_model_score ? "var(--success-green)" : "var(--text-secondary)" }}>
                    {scoringText}
                  </span>
                  {baselineHit && (
                    <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 2, background: baselineHit === "命中" ? "rgba(53,217,155,0.15)" : "rgba(255,107,107,0.15)", color: baselineHit === "命中" ? "var(--success-green)" : "var(--risk-red)" }}>
                      基线{baselineHit}
                    </span>
                  )}
                  {errorType && <span style={{ fontSize: 11, color: "var(--risk-red)", marginLeft: "auto" }}>{errorType}</span>}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* F. Action Area */}
      <CollapsibleSection title="操作" defaultOpen={false}>
        <div className="action-grid">
          <ActionButton
            label="更新今日数据"
            enabled={dailyOpenBtn.enabled}
            disabledReason={dailyOpenBtn.reason}
            loading={dailyOpenMutation.isPending}
            estimatedCalls={dailyOpenBtn.estimated_calls}
            onClick={() => dailyOpenMutation.mutate({})}
            variant="primary"
          />
          <ActionButton
            label="运行 AI 预测"
            enabled={aiBtn.enabled}
            disabledReason={aiBtn.reason}
            loading={preMatchMutation.isPending}
            estimatedCalls={aiBtn.estimated_calls}
            warningText={aiBtn.needs_ai && aiBtn.needs_ai > 0 ? `将处理 ${aiBtn.needs_ai} 场比赛，调用外部 API 产生费用` : undefined}
            onClick={() => preMatchMutation.mutate({ include_ai: true })}
            variant="warning"
          />
          <ActionButton
            label="一键更新全部"
            enabled={fullBtn.enabled}
            disabledReason={fullBtn.reason}
            loading={fullMutation.isPending}
            estimatedCalls={fullBtn.estimated_calls}
            warningText="包含 AI 预测步骤，会调用外部 API 产生费用"
            onClick={() => fullMutation.mutate({})}
            variant="danger"
          />
        </div>

        {/* Mutation feedback */}
        {dailyOpenMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>更新失败：{dailyOpenMutation.error instanceof Error ? dailyOpenMutation.error.message : "未知错误"}</div>}
        {preMatchMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>AI 预测失败：{preMatchMutation.error instanceof Error ? preMatchMutation.error.message : "未知错误"}</div>}
        {fullMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>全流程失败：{fullMutation.error instanceof Error ? fullMutation.error.message : "未知错误"}</div>}
        {dailyOpenMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>数据更新完成</div>}
        {preMatchMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>AI 预测完成</div>}
        {fullMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>全流程完成</div>}
      </CollapsibleSection>

      {/* G. Model Performance Summary */}
      <CollapsibleSection title="模型性能概览" defaultOpen={false}>
        {modelSummary ? (
          <div className="metric-grid">
            <MetricCard label="评分样本" value={modelSummary.minSample} tone={modelSummary.insufficient ? "warn" : "ok"} note={modelSummary.insufficient ? "样本不足（需≥5）" : "样本充分"} />
            <MetricCard label="默认模型" value={modelSummary.insufficient ? "baseline" : modelSummary.currentDefault} tone={modelSummary.insufficient ? "neutral" : "ok"} />
            <MetricCard label="AI 评分" value={modelSummary.aiScoreStatus} tone={modelSummary.aiScoreStatus === "有帮助" ? "ok" : modelSummary.aiScoreStatus === "有损害" ? "error" : "neutral"} />
          </div>
        ) : (
          <EmptyState>加载模型性能数据...</EmptyState>
        )}
      </CollapsibleSection>

      {/* H. Recent Run Log */}
      {runs.length > 0 && (
        <CollapsibleSection title="最近运行记录" badge={`${runs.length} 条`} defaultOpen={false}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {runs.slice(0, 3).map((run) => {
              const typeLabel: Record<string, string> = { daily_open: "每日开盘", pre_match: "赛前更新", post_match: "赛后复盘", lock: "赛前决策快照", full: "一键全流程" };
              const statusColor: Record<string, string> = { completed: "var(--success-green)", running: "var(--accent-yellow)", failed: "var(--risk-red)", pending: "var(--text-secondary)", partial_success: "var(--accent-yellow)" };
              const statusLabel: Record<string, string> = { completed: "完成", running: "运行中", failed: "失败", pending: "等待", partial_success: "部分完成" };
              return (
                <div key={run.id} className="run-log-entry">
                  {statusDot(statusColor[run.status] ?? "var(--text-secondary)")}
                  <span style={{ fontWeight: 600 }}>{typeLabel[run.workflow_type] ?? run.workflow_type}</span>
                  <span style={{ color: statusColor[run.status] }}>{statusLabel[run.status] ?? run.status}</span>
                  <span style={{ color: "var(--text-secondary)" }}>{formatChinaTimeShort(run.started_at)}</span>
                  {run.duration_seconds != null && (
                    <span style={{ color: "var(--text-secondary)" }}>
                      {run.duration_seconds < 60 ? `${run.duration_seconds.toFixed(0)}秒` : `${Math.floor(run.duration_seconds / 60)}分${Math.floor(run.duration_seconds % 60)}秒`}
                    </span>
                  )}
                  {run.error_message && <span style={{ color: "var(--risk-red)", marginLeft: "auto", fontSize: 11 }}>{run.error_message}</span>}
                </div>
              );
            })}
          </div>
        </CollapsibleSection>
      )}

      <MatchDetailDrawer
        open={selectedMatch != null}
        match={selectedMatch}
        onClose={() => setSelectedMatch(null)}
      />
    </div>
  );
}
