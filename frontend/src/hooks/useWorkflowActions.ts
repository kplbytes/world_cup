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
import { AUTO_DAILY_OPEN_PARAMS } from "../utils/workflow";

interface UseWorkflowActionsOptions {
  /** Number of recent workflow runs to fetch (default 5) */
  runsLimit?: number;
  /** Additional query keys to invalidate after mutations */
  extraInvalidateKeys?: string[][];
}

export function useWorkflowActions(options: UseWorkflowActionsOptions = {}) {
  const { runsLimit = 5, extraInvalidateKeys = [] } = options;
  const queryClient = useQueryClient();
  const autoTriggered = useRef(false);

  const statusQuery = useQuery({
    queryKey: ["workflow-status"],
    queryFn: getWorkflowStatus,
    staleTime: 30_000,
  });

  const runsQuery = useQuery({
    queryKey: ["workflow-runs"],
    queryFn: () => getWorkflowRuns(runsLimit),
    staleTime: 30_000,
  });

  const invalidateAll = () => {
    queryClient.invalidateQueries({ queryKey: ["workflow-status"] });
    queryClient.invalidateQueries({ queryKey: ["workflow-runs"] });
    for (const key of extraInvalidateKeys) {
      queryClient.invalidateQueries({ queryKey: key });
    }
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

  const fullMutation = useMutation({
    mutationFn: triggerFullWorkflow,
    onSuccess: invalidateAll,
  });

  useEffect(() => {
    if (autoTriggered.current) return;
    if (statusQuery.data?.recommended_action !== "run_daily_open_workflow") return;
    autoTriggered.current = true;
    dailyOpenMutation.mutate(AUTO_DAILY_OPEN_PARAMS);
  }, [statusQuery.data?.recommended_action]);

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
