import React from "react";
import type { WorkflowStepInfo } from "../types";

/** Render a small coloured dot indicator. */
export function statusDot(color: string): React.ReactElement {
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

/** Format a duration in seconds to a human-readable string. */
export function fmtDuration(seconds: number | null): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds.toFixed(0)}秒`;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}分${s}秒`;
}

const STEP_LABELS: Record<string, string> = {
  refresh_results: "同步赛果",
  post_match_recompute: "赛后重算",
  post_match_score: "赛后评分",
  pre_match_recompute: "赛前重算",
  ai_prediction: "AI 预测",
  ensemble_generation: "Ensemble",
  lock_predictions: "赛前快照",
  accuracy_command_update: "复盘汇总",
  artifact_generation: "产物汇总",
};

const STEP_STATUS_LABELS: Record<string, string> = {
  pending: "等待",
  running: "运行中",
  success: "完成",
  partial_success: "部分完成",
  failed: "失败",
  skipped: "跳过",
};

function asCount(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function asReasonMap(value: unknown): Record<string, number> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {};
  return Object.fromEntries(
    Object.entries(value).filter(([, count]) => typeof count === "number" && Number.isFinite(count) && count > 0),
  );
}

function formatReasonLabel(reason: string): string {
  const map: Record<string, string> = {
    teams_tbd: "待定对阵",
    missing_system_prediction: "缺少基线快照",
    ensemble_error: "集成异常",
    cooldown: "冷却跳过",
  };
  return map[reason] ?? reason;
}

export function workflowStepLabel(stepName: string): string {
  return STEP_LABELS[stepName] ?? stepName;
}

export function workflowStepStatusLabel(status: string): string {
  return STEP_STATUS_LABELS[status] ?? status;
}

export function workflowStepSummaryText(step: WorkflowStepInfo): string | null {
  const summary = step.summary;
  if (!summary || typeof summary !== "object") return null;

  const success = asCount(summary.success);
  const failed = asCount(summary.failed);
  const skipped = asCount(summary.skipped);
  const apiCalls = asCount(summary.api_calls);
  const lockedCount = asCount(summary.locked_count);
  const reason = typeof summary.reason === "string" ? summary.reason : null;
  const skippedReasons = asReasonMap(summary.skipped_reasons);
  const failedReasons = asReasonMap(summary.failed_reasons);

  const parts: string[] = [];
  if (success && success > 0) parts.push(`成功 ${success}`);
  if (failed && failed > 0) parts.push(`失败 ${failed}`);
  if (skipped && skipped > 0) parts.push(`跳过 ${skipped}`);
  if (apiCalls && apiCalls > 0) parts.push(`调用 ${apiCalls}`);
  if (lockedCount && lockedCount > 0) parts.push(`锁定 ${lockedCount}`);

  for (const [key, count] of Object.entries(skippedReasons)) {
    parts.push(`${formatReasonLabel(key)} ${count}`);
  }
  for (const [key, count] of Object.entries(failedReasons)) {
    parts.push(`${formatReasonLabel(key)} ${count}`);
  }

  if (parts.length > 0) return parts.join(" · ");
  return reason;
}
