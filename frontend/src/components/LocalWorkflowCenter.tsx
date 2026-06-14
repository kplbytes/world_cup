import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getWorkflowStatus,
  triggerDailyOpen,
  triggerPreMatch,
  triggerPostMatch,
  triggerLock,
  triggerFullWorkflow,
  getWorkflowRuns,
} from "../api";
import type { WorkflowStatus, WorkflowRunInfo, ButtonState } from "../types";
import { formatChinaTimeShort } from "../utils/time";

const STATUS_COLOR: Record<string, string> = {
  completed: "var(--mint)",
  running: "var(--amber)",
  failed: "var(--coral)",
  pending: "var(--muted)",
};

const STATUS_LABEL: Record<string, string> = {
  completed: "已完成",
  running: "运行中",
  failed: "失败",
  pending: "等待中",
};

const WORKFLOW_TYPE_LABEL: Record<string, string> = {
  daily_open: "每日开盘",
  pre_match: "赛前更新",
  post_match: "赛后复盘",
  lock: "赛前决策快照",
  full: "一键全流程",
};

const AUTO_DAILY_OPEN_PARAMS = {
  with_ai: true,
  with_ensemble: true,
  auto_lock: true,
  only_missing: true,
  limit: 10,
  hours: 48,
  since_hours: 24,
} as const;

function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}分${s}秒`;
}

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
      }}
    />
  );
}

function ButtonHelper({ btn, loading }: { btn: ButtonState | undefined; loading: boolean }) {
  if (!btn) return null;
  if (!btn.enabled) {
    return <div style={{ fontSize: 11, color: "var(--coral)", marginTop: 6 }}>不可运行：{btn.reason}</div>;
  }
  if (btn.estimated_calls && btn.estimated_calls > 0) {
    return <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6 }}>预计处理 {btn.needs_ai ?? 0} 场，最多调用 {btn.estimated_calls} 次模型</div>;
  }
  return null;
}

export default function LocalWorkflowCenter() {
  const queryClient = useQueryClient();
  const [autoTriggered, setAutoTriggered] = useState(false);

  const statusQuery = useQuery({
    queryKey: ["workflow-status"],
    queryFn: getWorkflowStatus,
    staleTime: 30_000,
  });

  const runsQuery = useQuery({
    queryKey: ["workflow-runs"],
    queryFn: () => getWorkflowRuns(10),
    staleTime: 30_000,
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
    queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
  };

  const dailyOpenMutation = useMutation({
    mutationFn: triggerDailyOpen,
    onSuccess: invalidateAll,
  });

  const preMatchMutation = useMutation({
    mutationFn: triggerPreMatch,
    onSuccess: invalidateAll,
  });

  const postMatchMutation = useMutation({
    mutationFn: triggerPostMatch,
    onSuccess: invalidateAll,
  });

  const lockMutation = useMutation({
    mutationFn: triggerLock,
    onSuccess: invalidateAll,
  });

  const fullMutation = useMutation({
    mutationFn: triggerFullWorkflow,
    onSuccess: invalidateAll,
  });

  useEffect(() => {
    if (autoTriggered) return;
    if (statusQuery.data?.recommended_action !== "run_daily_open_workflow") return;
    setAutoTriggered(true);
    dailyOpenMutation.mutate(AUTO_DAILY_OPEN_PARAMS);
  }, [autoTriggered, dailyOpenMutation, statusQuery.data?.recommended_action]);

  const status = statusQuery.data as WorkflowStatus | undefined;
  const btnStates = status?.button_states;
  const anyRunning =
    dailyOpenMutation.isPending ||
    preMatchMutation.isPending ||
    postMatchMutation.isPending ||
    lockMutation.isPending ||
    fullMutation.isPending;

  const runs = (runsQuery.data as { runs?: WorkflowRunInfo[] } | undefined)
    ?.runs ?? [];

  const todayStatusColor =
    status?.today_status === "completed"
      ? "var(--mint)"
      : status?.today_status === "running"
        ? "var(--amber)"
        : "var(--coral)";

  return (
    <div className="decision-view" style={{ maxWidth: 900 }}>
      {/* 顶部通知 */}
      <div
        style={{
          fontSize: 13,
          color: "var(--mint)",
          background: "oklch(34% .025 160 / .1)",
          padding: "12px 16px",
          borderLeft: "3px solid var(--mint)",
          marginBottom: 20,
          lineHeight: 1.6,
        }}
      >
        每天第一次打开页面会自动更新赛果、复盘、预测，并在配置允许时自动运行 AI。60 分钟内重复打开不会重复执行。
      </div>

      {/* 1. 今日运行状态 */}
      <div className="decision-section">
        <h3>今日运行状态</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--line)",
            padding: 16,
          }}
        >
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 12,
            }}
          >
            <span style={{ fontSize: 14, fontWeight: 600 }}>
              {statusDot(todayStatusColor)}
              {status?.today_status === "completed"
                ? "今日流程已完成"
                : status?.today_status === "running"
                  ? "今日流程运行中"
                  : "今日流程未运行"}
            </span>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              上次运行: {formatChinaTimeShort(status?.last_run_at ?? null)}
            </span>
          </div>
          {status?.recommended_action && (
            <div
              style={{
                fontSize: 12,
                color: "var(--amber)",
                background: "oklch(34% .025 80 / .1)",
                padding: "8px 12px",
                borderLeft: "2px solid var(--amber)",
              }}
            >
              建议操作: {status.recommended_action}
            </div>
          )}
        </div>
      </div>

      {/* 2. 昨晚比赛复盘 */}
      <div className="decision-section">
        <h3>昨晚比赛复盘</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--line)",
            padding: 16,
          }}
        >
          <div
            style={{
              display: "flex",
              gap: 24,
              alignItems: "center",
              marginBottom: 12,
            }}
          >
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                比赛场次
              </span>
              <div style={{ fontSize: 22, fontWeight: 700 }}>
                {status?.yesterday_matches?.count ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                已评分
              </span>
              <div style={{ fontSize: 22, fontWeight: 700, color: "var(--mint)" }}>
                {status?.yesterday_matches?.scored ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                需要复盘
              </span>
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color: status?.yesterday_matches?.needs_review
                    ? "var(--coral)"
                    : "var(--mint)",
                }}
              >
                {status?.yesterday_matches?.needs_review ? "是" : "否"}
              </div>
            </div>
          </div>
          <button
            className="revision-stamp button"
            disabled={anyRunning || (btnStates?.post_match && !btnStates.post_match.enabled)}
            onClick={() => postMatchMutation.mutate({})}
            style={{
              border: 0,
              background: "var(--amber)",
              color: "oklch(22% .04 80)",
              padding: "10px 16px",
              fontWeight: 600,
              cursor: anyRunning || (btnStates?.post_match && !btnStates.post_match.enabled) ? "not-allowed" : "pointer",
              opacity: anyRunning || (btnStates?.post_match && !btnStates.post_match.enabled) ? 0.55 : 1,
            }}
          >
            {postMatchMutation.isPending ? "复盘运行中..." : "运行赛后复盘"}
          </button>
          <ButtonHelper btn={btnStates?.post_match} loading={anyRunning} />
          {postMatchMutation.isError && (
            <div style={{ color: "var(--coral)", fontSize: 12, marginTop: 8 }}>
              错误:{" "}
              {postMatchMutation.error instanceof Error
                ? postMatchMutation.error.message
                : "未知错误"}
            </div>
          )}
          {postMatchMutation.data && (
            <div style={{ color: "var(--mint)", fontSize: 12, marginTop: 8 }}>
              复盘完成
            </div>
          )}
        </div>
      </div>

      {/* 3. 今天比赛预测 */}
      <div className="decision-section">
        <h3>今天比赛预测</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--line)",
            padding: 16,
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                24h 内比赛
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.upcoming_matches?.count_24h ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                48h 内比赛
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.upcoming_matches?.count_48h ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                基线就绪
              </span>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--mint)" }}>
                {status?.upcoming_matches?.baseline_ready ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                AI 就绪
              </span>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--mint)" }}>
                {status?.upcoming_matches?.ai_ready ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                集成就绪
              </span>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--mint)" }}>
                {status?.upcoming_matches?.ensemble_ready ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                需要 AI
              </span>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  color:
                    (status?.upcoming_matches?.needs_ai ?? 0) > 0
                      ? "var(--amber)"
                      : "var(--mint)",
                }}
              >
                {status?.upcoming_matches?.needs_ai ?? "-"}
              </div>
            </div>
          </div>
          <button
            disabled={anyRunning || (btnStates?.pre_match && !btnStates.pre_match.enabled)}
            onClick={() => preMatchMutation.mutate({})}
            style={{
              border: 0,
              background: "var(--mint)",
              color: "oklch(20% .04 160)",
              padding: "10px 16px",
              fontWeight: 600,
              cursor: anyRunning || (btnStates?.pre_match && !btnStates.pre_match.enabled) ? "not-allowed" : "pointer",
              opacity: anyRunning || (btnStates?.pre_match && !btnStates.pre_match.enabled) ? 0.55 : 1,
            }}
          >
            {preMatchMutation.isPending
              ? "赛前更新运行中..."
              : "运行赛前更新（不含AI）"}
          </button>
          <ButtonHelper btn={btnStates?.pre_match} loading={anyRunning} />
          {preMatchMutation.isError && (
            <div style={{ color: "var(--coral)", fontSize: 12, marginTop: 8 }}>
              错误:{" "}
              {preMatchMutation.error instanceof Error
                ? preMatchMutation.error.message
                : "未知错误"}
            </div>
          )}
          {preMatchMutation.data && (
            <div style={{ color: "var(--mint)", fontSize: 12, marginTop: 8 }}>
              赛前更新完成
            </div>
          )}
        </div>
      </div>

      {/* 4. AI 预测 */}
      <div className="decision-section">
        <h3>AI 预测</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--line)",
            padding: 16,
          }}
        >
          <div
            style={{
              display: "flex",
              gap: 24,
              alignItems: "center",
              marginBottom: 12,
            }}
          >
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                需要 AI 预测
              </span>
              <div
                style={{
                  fontSize: 22,
                  fontWeight: 700,
                  color:
                    (status?.upcoming_matches?.needs_ai ?? 0) > 0
                      ? "var(--amber)"
                      : "var(--mint)",
                }}
              >
                {status?.upcoming_matches?.needs_ai ?? 0}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                AI 已就绪
              </span>
              <div style={{ fontSize: 22, fontWeight: 700, color: "var(--mint)" }}>
                {status?.upcoming_matches?.ai_ready ?? 0}
              </div>
            </div>
          </div>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(120px, 1fr))",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                今日已调用
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.ai_stats?.today_ai_calls ?? 0}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                今日失败
              </span>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  color:
                    (status?.ai_stats?.today_ai_failed ?? 0) > 0
                      ? "var(--coral)"
                      : undefined,
                }}
              >
                {status?.ai_stats?.today_ai_failed ?? 0}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                今日跳过
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.ai_stats?.today_ai_skipped ?? 0}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                冷却跳过
              </span>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  color: status?.ai_stats?.cooldown_skipped
                    ? "var(--amber)"
                    : undefined,
                }}
              >
                {status?.ai_stats?.cooldown_skipped ? "是" : "否"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                仅补缺失跳过
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.ai_stats?.only_missing_skipped ?? 0}
              </div>
            </div>
          </div>
          <div
            style={{
              fontSize: 12,
              color: "var(--amber)",
              background: "oklch(34% .025 80 / .1)",
              padding: "8px 12px",
              borderLeft: "2px solid var(--amber)",
              marginBottom: 12,
            }}
          >
            注意: AI 预测会调用外部 API，产生费用。请确认后再运行。
          </div>
          <button
            disabled={anyRunning || (btnStates?.ai_prediction && !btnStates.ai_prediction.enabled)}
            onClick={() => dailyOpenMutation.mutate({ include_ai: true })}
            style={{
              border: 0,
              background: "var(--amber)",
              color: "oklch(22% .04 80)",
              padding: "10px 16px",
              fontWeight: 600,
              cursor: anyRunning || (btnStates?.ai_prediction && !btnStates.ai_prediction.enabled) ? "not-allowed" : "pointer",
              opacity: anyRunning || (btnStates?.ai_prediction && !btnStates.ai_prediction.enabled) ? 0.55 : 1,
            }}
          >
            {dailyOpenMutation.isPending
              ? "AI 预测运行中..."
              : "运行 AI 预测（含费用）"}
          </button>
          <ButtonHelper btn={btnStates?.ai_prediction} loading={anyRunning} />
          {dailyOpenMutation.isError && (
            <div style={{ color: "var(--coral)", fontSize: 12, marginTop: 8 }}>
              错误:{" "}
              {dailyOpenMutation.error instanceof Error
                ? dailyOpenMutation.error.message
                : "未知错误"}
            </div>
          )}
          {dailyOpenMutation.data && (
            <div style={{ color: "var(--mint)", fontSize: 12, marginTop: 8 }}>
              AI 预测完成
            </div>
          )}
        </div>
      </div>

      {/* 5. 赛前决策快照 */}
      <div className="decision-section">
        <h3>赛前决策快照</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "1px solid var(--line)",
            padding: 16,
          }}
        >
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))",
              gap: 12,
              marginBottom: 12,
            }}
          >
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                即将开赛
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.lock_status?.matches_near_kickoff ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                已锁定
              </span>
              <div style={{ fontSize: 20, fontWeight: 700, color: "var(--mint)" }}>
                {status?.lock_status?.locked ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                需要锁定
              </span>
              <div
                style={{
                  fontSize: 20,
                  fontWeight: 700,
                  color:
                    (status?.lock_status?.needs_lock ?? 0) > 0
                      ? "var(--coral)"
                      : "var(--mint)",
                }}
              >
                {status?.lock_status?.needs_lock ?? "-"}
              </div>
            </div>
            <div>
              <span style={{ color: "var(--muted)", fontSize: 11 }}>
                仅实时
              </span>
              <div style={{ fontSize: 20, fontWeight: 700 }}>
                {status?.lock_status?.real_time_only ?? "-"}
              </div>
            </div>
          </div>
          <button
            disabled={anyRunning || (btnStates?.lock && !btnStates.lock.enabled)}
            onClick={() => lockMutation.mutate({})}
            style={{
              border: 0,
              background: "var(--coral)",
              color: "oklch(98% .01 95)",
              padding: "10px 16px",
              fontWeight: 600,
              cursor: anyRunning || (btnStates?.lock && !btnStates.lock.enabled) ? "not-allowed" : "pointer",
              opacity: anyRunning || (btnStates?.lock && !btnStates.lock.enabled) ? 0.55 : 1,
            }}
          >
            {lockMutation.isPending ? "锁定运行中..." : "锁定即将开赛比赛"}
          </button>
          <ButtonHelper btn={btnStates?.lock} loading={anyRunning} />
          {lockMutation.isError && (
            <div style={{ color: "var(--coral)", fontSize: 12, marginTop: 8 }}>
              错误:{" "}
              {lockMutation.error instanceof Error
                ? lockMutation.error.message
                : "未知错误"}
            </div>
          )}
          {lockMutation.data && (
            <div style={{ color: "var(--mint)", fontSize: 12, marginTop: 8 }}>
              锁定完成
            </div>
          )}
        </div>
      </div>

      {/* 6. 一键更新 */}
      <div className="decision-section">
        <h3>一键全流程</h3>
        <div
          style={{
            background: "var(--paper-2)",
            border: "2px solid var(--coral)",
            padding: 16,
          }}
        >
          <div
            style={{
              fontSize: 12,
              color: "var(--coral)",
              background: "oklch(34% .025 30 / .15)",
              padding: "8px 12px",
              borderLeft: "2px solid var(--coral)",
              marginBottom: 12,
            }}
          >
            警告: 一键全流程包含 AI 预测步骤，会调用外部 API 产生费用。请确认后再运行。
          </div>
          <button
            disabled={anyRunning || (btnStates?.full && !btnStates.full.enabled)}
            onClick={() => fullMutation.mutate({})}
            style={{
              border: 0,
              background: "var(--coral)",
              color: "oklch(98% .01 95)",
              padding: "12px 24px",
              fontWeight: 700,
              fontSize: 14,
              cursor: anyRunning || (btnStates?.full && !btnStates.full.enabled) ? "not-allowed" : "pointer",
              opacity: anyRunning || (btnStates?.full && !btnStates.full.enabled) ? 0.55 : 1,
            }}
          >
            {fullMutation.isPending
              ? "全流程运行中..."
              : "运行一键全流程（含 AI，产生费用）"}
          </button>
          <ButtonHelper btn={btnStates?.full} loading={anyRunning} />
          {fullMutation.isError && (
            <div style={{ color: "var(--coral)", fontSize: 12, marginTop: 8 }}>
              错误:{" "}
              {fullMutation.error instanceof Error
                ? fullMutation.error.message
                : "未知错误"}
            </div>
          )}
          {fullMutation.data && (
            <div style={{ color: "var(--mint)", fontSize: 12, marginTop: 8 }}>
              全流程完成
            </div>
          )}
        </div>
      </div>

      {/* 7. 工作流日志 */}
      <div className="decision-section">
        <h3>工作流日志</h3>
        {runsQuery.isLoading && (
          <div style={{ color: "var(--muted)", fontSize: 13, padding: 12 }}>
            加载中...
          </div>
        )}
        {runsQuery.isError && (
          <div style={{ color: "var(--coral)", fontSize: 13, padding: 12 }}>
            加载失败:{" "}
            {runsQuery.error instanceof Error
              ? runsQuery.error.message
              : "未知错误"}
          </div>
        )}
        {runs.length === 0 && !runsQuery.isLoading && (
          <div style={{ color: "var(--muted)", fontSize: 13, padding: 12 }}>
            暂无运行记录
          </div>
        )}
        {runs.map((run) => (
          <div
            key={run.id}
            style={{
              background: "var(--paper-2)",
              border: "1px solid var(--line)",
              padding: 14,
              marginBottom: 8,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 8,
              }}
            >
              <span style={{ fontWeight: 600, fontSize: 13 }}>
                {statusDot(STATUS_COLOR[run.status] ?? "var(--muted)")}
                {WORKFLOW_TYPE_LABEL[run.workflow_type] ?? run.workflow_type}
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: STATUS_COLOR[run.status] ?? "var(--muted)",
                  fontWeight: 600,
                }}
              >
                {STATUS_LABEL[run.status] ?? run.status}
              </span>
            </div>
            <div
              style={{
                display: "flex",
                gap: 16,
                fontSize: 11,
                color: "var(--muted)",
                marginBottom: run.steps?.length ? 8 : 0,
              }}
            >
              <span>触发: {run.trigger_source}</span>
              <span>开始: {formatChinaTimeShort(run.started_at)}</span>
              <span>耗时: {fmtDuration(run.duration_seconds)}</span>
            </div>
            {run.error_message && (
              <div
                style={{
                  fontSize: 11,
                  color: "var(--coral)",
                  background: "oklch(34% .025 30 / .1)",
                  padding: "6px 10px",
                  marginBottom: 8,
                }}
              >
                {run.error_message}
              </div>
            )}
            {run.steps?.length > 0 && (
              <div
                style={{
                  display: "flex",
                  gap: 4,
                  flexWrap: "wrap",
                }}
              >
                {run.steps.map((step, i) => (
                  <span
                    key={i}
                    style={{
                      fontSize: 10,
                      padding: "2px 8px",
                      borderRadius: 2,
                      background:
                        step.status === "completed"
                          ? "oklch(34% .025 160 / .3)"
                          : step.status === "failed"
                            ? "oklch(34% .025 30 / .3)"
                            : step.status === "running"
                              ? "oklch(34% .025 80 / .3)"
                              : "oklch(34% .025 160 / .15)",
                      color:
                        step.status === "completed"
                          ? "var(--mint)"
                          : step.status === "failed"
                            ? "var(--coral)"
                            : step.status === "running"
                              ? "var(--amber)"
                              : "var(--muted)",
                    }}
                  >
                    {step.step_name}
                    {step.duration_seconds != null
                      ? ` (${fmtDuration(step.duration_seconds)})`
                      : ""}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
