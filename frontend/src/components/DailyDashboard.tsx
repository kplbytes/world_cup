import React, { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  getAccuracyCommandCenter,
  getDecisionSnapshotStatus,
} from "../api";
import type { Match, DecisionSnapshotStatus } from "../types";
import { formatChinaTimeShort, isFinishedMatch, isUpcomingMatch, isWithinNextHoursChina, isLiveMatch } from "../utils/time";
import { getTeamDisplayFromRef } from "../utils/teamNames";
import { directionLabel } from "../utils/recommendation";
import { statusDot, fmtDuration, workflowStepLabel, workflowStepStatusLabel, workflowStepSummaryText } from "../utils/workflow";
import { useWorkflowActions } from "../hooks/useWorkflowActions";
import ActionButton from "./ActionButton";
import MatchSummaryCard from "./MatchSummaryCard";
import MatchDetailDrawer from "./MatchDetailDrawer";
import StatusStrip from "./ui/StatusStrip";
import type { StatusItem } from "./ui/StatusStrip";
import SectionCard from "./ui/SectionCard";
import MetricCard from "./ui/MetricCard";
import EmptyState from "./ui/EmptyState";
import WorkflowProgressBar from "./ui/WorkflowProgressBar";

// ── Helpers ──────────────────────────────────────────────────────────

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
          className="btn-ghost"
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

interface DailyDashboardProps {
  dashboardData: import("../types").Dashboard | undefined;
}

function WorkflowStepSummaryList({ steps, limit = 4 }: { steps: import("../types").WorkflowStepInfo[]; limit?: number }) {
  const visibleSteps = steps.filter((step) => {
    if (step.status === "pending") return false;
    if (step.status === "skipped") return Boolean(workflowStepSummaryText(step) || step.error_message);
    return true;
  }).slice(0, limit);

  if (visibleSteps.length === 0) return null;

  return (
    <div className="workflow-step-summary-list">
      {visibleSteps.map((step) => {
        const summaryText = workflowStepSummaryText(step);
        return (
          <div key={step.step_name} className="workflow-step-summary-item">
            <span className={`workflow-step-summary-item__status workflow-step-summary-item__status--${step.status}`}>
              {workflowStepStatusLabel(step.status)}
            </span>
            <span className="workflow-step-summary-item__label">{workflowStepLabel(step.step_name)}</span>
            {summaryText ? <span className="workflow-step-summary-item__text">{summaryText}</span> : null}
            {!summaryText && step.duration_seconds != null ? (
              <span className="workflow-step-summary-item__text">{fmtDuration(step.duration_seconds)}</span>
            ) : null}
            {step.error_message ? <span className="workflow-step-summary-item__error">{step.error_message}</span> : null}
          </div>
        );
      })}
    </div>
  );
}

export default function DailyDashboard({ dashboardData }: DailyDashboardProps) {
  const [selectedMatch, setSelectedMatch] = useState<Match | null>(null);

  const {
    status,
    runs,
    dailyOpenMutation,
    preMatchMutation,
    postMatchMutation,
    fullMutation,
    anyRunning,
    dailyOpenBtn,
    aiBtn,
    postMatchBtn,
    fullBtn,
  } = useWorkflowActions({
    runsLimit: 5,
    extraInvalidateKeys: [["dashboard"]],
  });
  const activeRun = status?.last_run?.status === "running" ? status.last_run : null;
  const activePercent = activeRun?.progress?.percent ?? null;
  const workflowProgress = (workflowType: string) =>
    activeRun?.workflow_type === workflowType ? activePercent : null;
  const workflowRunning = (workflowType: string) =>
    activeRun?.workflow_type === workflowType;
  const dailyOpenRunning = dailyOpenMutation.isPending || workflowRunning("daily_open");
  const postMatchRunning = postMatchMutation.isPending || workflowRunning("post_match");
  const preMatchRunning = preMatchMutation.isPending || workflowRunning("pre_match");
  const fullRunning = fullMutation.isPending || workflowRunning("full");

  const accQuery = useQuery({
    queryKey: ["accuracy-command-center"],
    queryFn: getAccuracyCommandCenter,
    staleTime: 60_000,
  });

  const snapshotQuery = useQuery({
    queryKey: ["decision-snapshot-status"],
    queryFn: getDecisionSnapshotStatus,
    staleTime: 30_000,
  });

  const snapshotStatus = snapshotQuery.data as DecisionSnapshotStatus | undefined;

  const future24hMatches = useMemo(() => {
    if (!dashboardData) return [];
    const now = new Date();
    const allMatches: Match[] = [];
    for (const group of dashboardData.groups) {
      for (const match of group.matches) {
        if (isUpcomingMatch(match, now) && isWithinNextHoursChina(match.kickoff, 24, now)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
    return allMatches;
  }, [dashboardData]);

  const future48hMatches = useMemo(() => {
    if (!dashboardData) return [];
    const now = new Date();
    const allMatches: Match[] = [];
    for (const group of dashboardData.groups) {
      for (const match of group.matches) {
        if (isUpcomingMatch(match, now) && isWithinNextHoursChina(match.kickoff, 48, now)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
    return allMatches;
  }, [dashboardData]);

  const finishedMatches = useMemo(() => {
    if (!dashboardData) return [];
    const allMatches: Match[] = [];
    for (const group of dashboardData.groups) {
      for (const match of group.matches) {
        if (isFinishedMatch(match)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(b.kickoff).getTime() - new Date(a.kickoff).getTime());
    return allMatches;
  }, [dashboardData]);

  const liveMatches = useMemo(() => {
    if (!dashboardData) return [];
    const allMatches: Match[] = [];
    const now = new Date();
    for (const group of dashboardData.groups) {
      for (const match of group.matches) {
        if (isLiveMatch(match, now)) {
          allMatches.push(match);
        }
      }
    }
    allMatches.sort((a, b) => new Date(a.kickoff).getTime() - new Date(b.kickoff).getTime());
    return allMatches;
  }, [dashboardData]);

  // Key matches (3-5 most noteworthy)
  const keyMatches = useMemo(() => {
    if (future24hMatches.length === 0) return [];
    const scored = future24hMatches.map((m) => {
      let score = 0;
      const pred = m.prediction;
      const market = m.market;
      if (pred?.base_home_win != null) {
        const baselineRec = directionLabel(pred.base_home_win, pred.base_draw ?? pred.draw, pred.base_away_win ?? pred.away_win);
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

  // ── Build status strip items ──
  const statusItems: StatusItem[] = useMemo(() => {
    if (!status) return [];
    const rawStatus = status.today_status;
    let todayLabel: string;
    let todayTone: StatusItem["tone"];
    if (rawStatus === "already_run" || rawStatus === "completed" || rawStatus === "success") {
      todayLabel = "已更新"; todayTone = "ok";
    } else if (rawStatus === "running") {
      todayLabel = "运行中"; todayTone = "warn";
    } else if (rawStatus === "needs_run" || rawStatus === "not_run") {
      todayLabel = "待运行"; todayTone = "error";
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
        const aiReadyCount = status.upcoming_matches?.ai_ready ?? 0;
        const covered = Math.min(aiReadyCount, totalToday);
        const missing = Math.max(0, totalToday - covered);

        // When workflow is running, show "等待中" instead of "未运行"
        const isWorkflowRunning = rawStatus === "running";

        // Use aiReadyCount (from upcoming_matches) as primary signal —
        // aiEffective resets at UTC midnight but aiReady persists
        const aiTone: StatusItem["tone"] = aiReadyCount > 0 && missing === 0 ? "ok" : aiReadyCount > 0 ? "warn" : aiFailed > 0 ? "error" : isWorkflowRunning ? "neutral" : "neutral";
        let aiValue: string;
        if (missing > 0 && aiFailed > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场，${missing} 场缺失；成功 ${aiStatus.success}，失败 ${aiFailed}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (missing > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场，${missing} 场缺失；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiFailed > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，失败 ${aiFailed}，参与 Ensemble ${aiEffective}`;
        } else if (aiParseError > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiReadyCount > 0) {
          aiValue = `有效覆盖 ${covered}/${totalToday} 场；成功 ${aiStatus.success}，解析错误 ${aiParseError}，参与 Ensemble ${aiEffective}`;
        } else if (aiAttempted > 0) {
          aiValue = `${aiAttempted} 场已调用，暂无有效结果`;
        } else if (isWorkflowRunning) {
          aiValue = "等待中";
        } else {
          aiValue = "未运行";
        }
        items.push({ label: "AI", value: aiValue, tone: aiTone });
      } else {
        items.push({ label: "AI", value: "未配置", tone: "neutral" });
      }
    } else {
      // Fallback to old ai_stats — use upcoming_matches.ai_ready as primary signal
      // (today_ai_calls resets at UTC midnight, but ai_ready persists)
      const aiReadyCount = status.upcoming_matches?.ai_ready ?? 0;
      const aiCalls = status.ai_stats?.today_ai_calls ?? 0;
      const isWorkflowRunning = rawStatus === "running";
      const aiValue = aiReadyCount > 0 ? `已运行（覆盖 ${aiReadyCount} 场）` : aiCalls > 0 ? `已运行 ${aiCalls}` : isWorkflowRunning ? "等待中" : "未运行";
      items.push({ label: "AI", value: aiValue, tone: aiReadyCount > 0 || aiCalls > 0 ? "ok" : "neutral" });
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
    if (rawStatus === "already_run" || rawStatus === "completed" || rawStatus === "success") {
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
        {status?.last_run?.progress ? (
          <div style={{ marginTop: 12 }}>
            <WorkflowProgressBar progress={status.last_run.progress} status={status.last_run.status} />
            {status.last_run.steps?.length ? (
              <div style={{ marginTop: 10 }}>
                <WorkflowStepSummaryList steps={status.last_run.steps} />
              </div>
            ) : null}
          </div>
        ) : null}
      </SectionCard>

      {/* B. Next Step Suggestion */}
      {nextStep && (
        <div className={`next-step${nextStep.tone === "ok" ? " next-step--ok" : nextStep.tone === "error" ? " next-step--error" : ""}`} style={{ marginBottom: 20, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
          <span>{nextStep.text}</span>
          {status?.next_action?.action === "run_daily_open_workflow" && (
            <button
              onClick={() => dailyOpenMutation.mutate({})}
              disabled={dailyOpenRunning || !dailyOpenBtn.enabled}
              style={{
                padding: "4px 14px", borderRadius: 6, border: "none", cursor: "pointer",
                background: dailyOpenRunning ? "var(--muted)" : "var(--accent-yellow)",
                color: "#000", fontWeight: 600, fontSize: 12, whiteSpace: "nowrap",
              }}
            >
              {dailyOpenRunning && workflowProgress("daily_open") != null
                ? `更新中 ${Math.round(workflowProgress("daily_open") ?? 0)}%`
                : dailyOpenRunning ? "更新中..." : "立即更新"}
            </button>
          )}
          {status?.next_action?.action === "run_ai_prediction" && (
            <button
              onClick={() => preMatchMutation.mutate({ with_ai: true })}
              disabled={preMatchRunning || !aiBtn.enabled}
              style={{
                padding: "4px 14px", borderRadius: 6, border: "none", cursor: "pointer",
                background: preMatchRunning ? "var(--muted)" : "var(--accent-yellow)",
                color: "#000", fontWeight: 600, fontSize: 12, whiteSpace: "nowrap",
              }}
            >
              {preMatchRunning && workflowProgress("pre_match") != null
                ? `运行中 ${Math.round(workflowProgress("pre_match") ?? 0)}%`
                : preMatchRunning ? "运行中..." : "运行 AI 预测"}
            </button>
          )}
        </div>
      )}

      {/* B2. Action Area - moved to top for better UX */}
      <SectionCard title="操作">
        <div className="action-grid">
          <ActionButton
            label="更新今日数据"
            enabled={dailyOpenBtn.enabled}
            disabledReason={dailyOpenBtn.reason}
            loading={dailyOpenRunning}
            progressPercent={workflowProgress("daily_open")}
            estimatedCalls={dailyOpenBtn.estimated_calls}
            onClick={() => dailyOpenMutation.mutate({})}
            variant="primary"
          />
          <ActionButton
            label="同步赛果"
            enabled={postMatchBtn.enabled}
            disabledReason={postMatchBtn.reason}
            loading={postMatchRunning}
            progressPercent={workflowProgress("post_match")}
            onClick={() => postMatchMutation.mutate({})}
            variant="primary"
          />
          <ActionButton
            label="运行 AI 预测"
            enabled={aiBtn.enabled}
            disabledReason={aiBtn.reason}
            loading={preMatchRunning}
            progressPercent={workflowProgress("pre_match")}
            estimatedCalls={aiBtn.estimated_calls}
            warningText={aiBtn.needs_ai && aiBtn.needs_ai > 0 ? `将处理 ${aiBtn.needs_ai} 场比赛，调用外部 API 产生费用` : undefined}
            onClick={() => preMatchMutation.mutate({ with_ai: true })}
            variant="warning"
          />
          <ActionButton
            label="一键更新全部"
            enabled={fullBtn.enabled}
            disabledReason={fullBtn.reason}
            loading={fullRunning}
            progressPercent={workflowProgress("full")}
            estimatedCalls={fullBtn.estimated_calls}
            warningText="包含 AI 预测步骤，会调用外部 API 产生费用"
            onClick={() => fullMutation.mutate({})}
            variant="danger"
          />
        </div>
        {/* Mutation feedback */}
        {dailyOpenMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>更新失败：{dailyOpenMutation.error instanceof Error ? dailyOpenMutation.error.message : "未知错误"}</div>}
        {postMatchMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>同步赛果失败：{postMatchMutation.error instanceof Error ? postMatchMutation.error.message : "未知错误"}</div>}
        {preMatchMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>AI 预测失败：{preMatchMutation.error instanceof Error ? preMatchMutation.error.message : "未知错误"}</div>}
        {fullMutation.isError && <div style={{ color: "var(--risk-red)", fontSize: 12, marginTop: 8, padding: "8px 12px", background: "rgba(255,107,107,0.08)", borderLeft: "2px solid var(--risk-red)" }}>全流程失败：{fullMutation.error instanceof Error ? fullMutation.error.message : "未知错误"}</div>}
        {dailyOpenMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>数据更新已开始</div>}
        {postMatchMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>赛果同步已开始</div>}
        {preMatchMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>AI 预测已开始</div>}
        {fullMutation.isSuccess && <div style={{ color: "var(--success-green)", fontSize: 12, marginTop: 8 }}>全流程已开始</div>}
      </SectionCard>

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
                const baselineRec = directionLabel(pred.base_home_win, pred.base_draw ?? pred.draw, pred.base_away_win ?? pred.away_win);
                const currentRec = directionLabel(pred.home_win, pred.draw, pred.away_win);
                if (baselineRec !== currentRec) reasons.push("系统内部调整差异较大");
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

      {/* C2. Live Matches */}
      {liveMatches.length > 0 && (
        <SectionCard title="进行中的比赛" badge={`${liveMatches.length} 场`}>
          <div className="today-match-grid">
            {liveMatches.map((m) => (
              <MatchSummaryCard
                key={m.id}
                match={m}
                onOpenDetails={setSelectedMatch}
                detailsOpen={selectedMatch?.id === m.id}
              />
            ))}
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
              const review = m.match_review;
              const hasPreMatchSnapshot = m.snapshot_status?.locked ?? false;
              const hasAiPrediction = Boolean(m.ai_prediction);
              const hasEnsemblePrediction = Boolean(m.ensemble_prediction);
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
              const resultLabel: Record<string, string> = { home: "主胜", draw: "平局", away: "客胜" };
              const sourceLabel: Record<string, string> = { baseline: "Baseline", ai: "AI", ensemble: "Ensemble", market: "市场" };
              return (
                <div key={m.id} className="yesterday-row" style={{ flexDirection: "column", alignItems: "flex-start", gap: 4 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, minWidth: 160 }}>{home} vs {away}</span>
                    <span style={{ fontWeight: 700, minWidth: 60 }}>{score}</span>
                    <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                      赛前快照：{hasPreMatchSnapshot ? "有" : "无"}
                    </span>
                    <span style={{ fontSize: 11, color: hasAiPrediction ? "var(--success-green)" : "var(--text-secondary)" }}>
                      AI：{hasAiPrediction ? "有" : "无"}
                    </span>
                    <span style={{ fontSize: 11, color: hasEnsemblePrediction ? "var(--success-green)" : "var(--text-secondary)" }}>
                      Ensemble：{hasEnsemblePrediction ? "有" : "无"}
                    </span>
                    <span style={{ fontSize: 11, color: m.snapshot_status?.participates_in_model_score ? "var(--success-green)" : "var(--text-secondary)" }}>
                      {scoringText}
                    </span>
                    {baselineHit && (
                      <span style={{ fontSize: 11, padding: "2px 6px", borderRadius: 2, background: baselineHit === "命中" ? "rgba(53,217,155,0.15)" : "rgba(255,107,107,0.15)", color: baselineHit === "命中" ? "var(--success-green)" : "var(--risk-red)" }}>
                        基线{baselineHit}
                      </span>
                    )}
                    {errorType && <span style={{ fontSize: 11, color: "var(--risk-red)" }}>{errorType}</span>}
                  </div>
                  {review && (
                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 11, color: "var(--text-secondary)", paddingLeft: 4 }}>
                      <span>赛果：{resultLabel[review.actual_result] ?? review.actual_result}</span>
                      {review.winner_hit != null && (
                        <span style={{ color: review.winner_hit ? "var(--success-green)" : "var(--risk-red)" }}>
                          方向{review.winner_hit ? "命中" : "偏差"}
                        </span>
                      )}
                      {(["baseline", "ai", "ensemble"] as const).map((src) => {
                        const r = review[src];
                        if (!r) return null;
                        return (
                          <span key={src}>
                            {sourceLabel[src]} Brier {r.brier.toFixed(4)} / 实际概率 {(r.actual_probability * 100).toFixed(1)}%
                          </span>
                        );
                      })}
                      {review.best_model && (
                        <span style={{ color: "var(--accent-yellow)" }}>最佳：{sourceLabel[review.best_model] ?? review.best_model}</span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </SectionCard>

      {/* F. Model Performance Summary */}
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
              const statusColor: Record<string, string> = { success: "var(--success-green)", running: "var(--accent-yellow)", failed: "var(--risk-red)", pending: "var(--text-secondary)", partial_success: "var(--accent-yellow)" };
              const statusLabel: Record<string, string> = { success: "完成", running: "运行中", failed: "失败", pending: "等待", partial_success: "部分完成" };
              return (
                <div key={run.id} className="run-log-entry">
                  <div className="run-log-entry__header">
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
                  {run.steps?.length ? <WorkflowStepSummaryList steps={run.steps} limit={3} /> : null}
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
