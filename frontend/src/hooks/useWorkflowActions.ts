import { useEffect, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  getWorkflowStatus,
  triggerDailyOpen,
  triggerPreMatch,
  triggerPostMatch,
  triggerFullWorkflow,
  getWorkflowRuns,
} from "../api";
import type { WorkflowStatus, WorkflowRunInfo, ButtonState } from "../types";

interface UseWorkflowActionsOptions {
  /** Number of recent workflow runs to fetch (default 5) */
  runsLimit?: number;
  /** Additional query keys to invalidate after workflow completion (not on trigger).
   * These are invalidated when the workflow transitions from running → success,
   * not immediately on mutation success — this avoids refetching stale data
   * while the background workflow is still executing. */
  extraInvalidateKeys?: string[][];
}

export function workflowStatusRefetchInterval(query: { state: { data?: WorkflowStatus } }): number | false {
  const status = query.state.data;
  if (!status) return false;
  return status.today_status === "running" || status.last_run?.status === "running"
    ? 2_000
    : false;
}

export function workflowRunsRefetchInterval(query: { state: { data?: { runs?: WorkflowRunInfo[] } } }): number | false {
  const runs = query.state.data?.runs ?? [];
  return runs.some((run) => run.status === "running") ? 2_000 : false;
}

export function useWorkflowActions(options: UseWorkflowActionsOptions = {}) {
  const { runsLimit = 5, extraInvalidateKeys = [] } = options;
  const queryClient = useQueryClient();
  // Track the previous workflow status so we can detect the running → success
  // transition and invalidate dashboard / projections only at that point.
  const prevStatusRef = useRef<string | undefined>(undefined);

  const statusQuery = useQuery({
    queryKey: ["workflow-status"],
    queryFn: getWorkflowStatus,
    staleTime: 30_000,
    refetchInterval: workflowStatusRefetchInterval,
  });

  const runsQuery = useQuery({
    queryKey: ["workflow-runs"],
    queryFn: () => getWorkflowRuns(runsLimit),
    staleTime: 30_000,
    refetchInterval: workflowRunsRefetchInterval,
  });

  // Watch for workflow completion: when the last_run status transitions from
  // "running" to "success" or "failed", invalidate the extra keys (dashboard,
  // projections, etc.) so the UI shows fresh data.
  useEffect(() => {
    const currentStatus = statusQuery.data?.last_run?.status;
    const prevStatus = prevStatusRef.current;
    prevStatusRef.current = currentStatus;

    if (prevStatus === "running" && currentStatus && currentStatus !== "running") {
      // Workflow just finished — invalidate all registered keys.
      for (const key of extraInvalidateKeys) {
        queryClient.invalidateQueries({ queryKey: key });
      }
    }
  }, [statusQuery.data?.last_run?.status, extraInvalidateKeys, queryClient]);

  // Only invalidate workflow queries immediately on mutation success.
  // Dashboard/projections are invalidated by the useEffect above when the
  // workflow actually completes.
  const invalidateWorkflowOnly = () => {
    queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
    queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
  };

  const dailyOpenMutation = useMutation({
    mutationFn: triggerDailyOpen,
    onSuccess: invalidateWorkflowOnly,
  });

  const preMatchMutation = useMutation({
    mutationFn: triggerPreMatch,
    onSuccess: invalidateWorkflowOnly,
  });

  const postMatchMutation = useMutation({
    mutationFn: triggerPostMatch,
    onSuccess: invalidateWorkflowOnly,
  });

  const fullMutation = useMutation({
    mutationFn: triggerFullWorkflow,
    onSuccess: invalidateWorkflowOnly,
  });

  const status = statusQuery.data as WorkflowStatus | undefined;
  const btnStates = status?.button_states;
  const anyRunning =
    dailyOpenMutation.isPending ||
    preMatchMutation.isPending ||
    postMatchMutation.isPending ||
    fullMutation.isPending;

  const runs =
    (runsQuery.data as { runs?: WorkflowRunInfo[] } | undefined)?.runs ?? [];

  const dailyOpenBtn: ButtonState = btnStates?.daily_open ?? { enabled: true, reason: "" };
  const aiBtn: ButtonState = btnStates?.ai_prediction ?? { enabled: true, reason: "" };
  const postMatchBtn: ButtonState = btnStates?.post_match ?? { enabled: true, reason: "" };
  const fullBtn: ButtonState = btnStates?.full ?? { enabled: true, reason: "" };

  return {
    statusQuery,
    runsQuery,
    status,
    btnStates,
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
  };
}
