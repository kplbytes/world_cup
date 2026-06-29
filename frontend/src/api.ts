import type {
  Dashboard, DecisionData, ModelScoreData,
  VersionScoreSummary, CalibrationBucket, MarketComparisonData,
  ModelRecommendation, DataQualityReport,
  AIModelStatus, AIPredictionItem, EnsemblePredictionItem,
  TeamProjection, BracketData, AIEvaluationResult,
  AccuracyCommandCenter,
  Match, ProfileEvaluation, TeamProfileEnvelope,
  MatchCountBreakdown, ErrorAttributionSummary,
  DecisionSnapshotStatus, ModelComparisonItem, KnockoutAudit,
  MatchScoreDetailItem, WorkflowStatus, WorkflowRunInfo, WorkflowTriggerResponse,
} from "./types";

const DEFAULT_TIMEOUT_MS = 30_000;

async function fetchWithTimeout(
  url: string,
  options?: RequestInit & { timeoutMs?: number }
): Promise<Response> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...fetchOptions } = options ?? {};
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, {
      ...fetchOptions,
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
  }
}

/**
 * P3-B: Typed API error that carries the HTTP status code.
 *
 * The retry policy in main.tsx reads `error.status` to skip retries on 4xx
 * responses. Plain `Error` instances don't carry this, so 404s were retried
 * 2x unnecessarily. `ApiError` extracts the status from the message (when
 * following the `"...: <status>"` convention) or accepts it explicitly.
 *
 * `isNotFound()` is used by P3-E (LookupError handling) to surface empty
 * states instead of generic error banners for 404 responses.
 */
export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = "ApiError";
    if (typeof status === "number") {
      this.status = status;
    } else {
      const match = message.match(/:\s*(\d+)\s*$/);
      this.status = match ? parseInt(match[1], 10) : 0;
    }
  }

  isNotFound(): boolean {
    return this.status === 404;
  }
}

export async function getDashboard(): Promise<Dashboard> {
  const response = await fetchWithTimeout("/api/dashboard");
  if (!response.ok) throw new ApiError(`Dashboard request failed: ${response.status}`);
  return response.json();
}

export async function getMatchDetail(matchId: string): Promise<Match> {
  const response = await fetchWithTimeout(`/api/matches/${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new ApiError(`Match detail failed: ${response.status}`);
  return response.json();
}

export async function getTeamProfile(teamId: string): Promise<TeamProfileEnvelope> {
  const response = await fetchWithTimeout(`/api/team-profiles/${encodeURIComponent(teamId)}`);
  if (!response.ok) throw new ApiError(`Team profile failed: ${response.status}`);
  return response.json();
}

export async function getProfileEvaluation(): Promise<ProfileEvaluation> {
  const response = await fetchWithTimeout("/api/team-profiles/evaluation");
  if (!response.ok) throw new ApiError(`Profile evaluation failed: ${response.status}`);
  return response.json();
}

export async function getDecision(): Promise<DecisionData> {
  const response = await fetchWithTimeout("/api/decision");
  if (!response.ok) throw new ApiError(`Decision request failed: ${response.status}`);
  return response.json();
}

export async function getModelScore(): Promise<ModelScoreData> {
  const response = await fetchWithTimeout("/api/model-score");
  if (!response.ok) throw new ApiError(`Model score request failed: ${response.status}`);
  return response.json();
}

export async function getModelScoreDetails(): Promise<{ details: MatchScoreDetailItem[]; exclusions: Array<{ match_id: string; home_team: string; away_team: string; reason: string }> }> {
  const response = await fetchWithTimeout("/api/model-score/details");
  if (!response.ok) throw new ApiError(`Model score details request failed: ${response.status}`);
  return response.json();
}

export async function refreshDashboard(): Promise<void> {
  const response = await fetchWithTimeout("/api/refresh", { method: "POST" });
  if (!response.ok) throw new ApiError(`Refresh failed: ${response.status}`);
}

// P2: Accuracy panel APIs
export async function getModelScoreByVersion(): Promise<{ versions: VersionScoreSummary[] }> {
  const response = await fetchWithTimeout("/api/model-score/by-version");
  if (!response.ok) throw new ApiError(`Model score by version failed: ${response.status}`);
  return response.json();
}

export async function getCalibration(): Promise<{ buckets: CalibrationBucket[] }> {
  const response = await fetchWithTimeout("/api/model-calibration");
  if (!response.ok) throw new ApiError(`Calibration request failed: ${response.status}`);
  return response.json();
}

export async function getMarketComparison(): Promise<MarketComparisonData> {
  const response = await fetchWithTimeout("/api/market-comparison");
  if (!response.ok) throw new ApiError(`Market comparison failed: ${response.status}`);
  return response.json();
}

export async function getModelRecommendation(): Promise<ModelRecommendation> {
  const response = await fetchWithTimeout("/api/model-recommendation");
  if (!response.ok) throw new ApiError(`Model recommendation failed: ${response.status}`);
  return response.json();
}

export async function getDataQuality(): Promise<DataQualityReport> {
  const response = await fetchWithTimeout("/api/data-quality");
  if (!response.ok) throw new ApiError(`Data quality check failed: ${response.status}`);
  return response.json();
}

// P2+: AI & Tournament APIs
export async function getAIModels(): Promise<{ enabled: boolean; models: AIModelStatus[] }> {
  const response = await fetchWithTimeout("/api/ai-models");
  if (!response.ok) throw new ApiError(`AI models request failed: ${response.status}`);
  return response.json();
}

export async function getAIPredictions(matchId: string): Promise<{ match_id: string; predictions: AIPredictionItem[] }> {
  const response = await fetchWithTimeout(`/api/ai-predictions?match_id=${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new ApiError(`AI predictions request failed: ${response.status}`);
  return response.json();
}

export async function runAIPrediction(matchId: string, modelVersion?: string, force = false): Promise<Record<string, unknown>> {
  let url = `/api/ai-predictions/run?match_id=${encodeURIComponent(matchId)}`;
  if (modelVersion && modelVersion !== "default") {
    url += `&model_version=${encodeURIComponent(modelVersion)}`;
  }
  if (force) {
    url += "&force=true";
  }
  const response = await fetchWithTimeout(url, { method: "POST", timeoutMs: 60_000 });
  if (!response.ok) throw new ApiError(`AI prediction run failed: ${response.status}`);
  return response.json();
}

export async function getEnsemble(matchId: string): Promise<{ match_id: string; predictions: EnsemblePredictionItem[] }> {
  const response = await fetchWithTimeout(`/api/ensemble?match_id=${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new ApiError(`Ensemble request failed: ${response.status}`);
  return response.json();
}

export async function runEnsemble(matchId: string): Promise<Record<string, unknown>> {
  const response = await fetchWithTimeout(`/api/ensemble/run?match_id=${encodeURIComponent(matchId)}`, {
    method: "POST",
    timeoutMs: 60_000,
  });
  if (!response.ok) throw new ApiError(`Ensemble run failed: ${response.status}`);
  return response.json();
}

export async function getTournamentBracket(): Promise<BracketData> {
  const response = await fetchWithTimeout("/api/tournament/bracket");
  if (!response.ok) throw new ApiError(`Tournament bracket failed: ${response.status}`);
  return response.json();
}

export async function getTournamentProjections(): Promise<{ projections: TeamProjection[] }> {
  const response = await fetchWithTimeout("/api/tournament/projections");
  if (!response.ok) throw new ApiError(`Tournament projections failed: ${response.status}`);
  return response.json();
}

export async function getAIEvaluation(): Promise<AIEvaluationResult> {
  const response = await fetchWithTimeout("/api/ai-evaluation");
  if (!response.ok) throw new ApiError(`AI evaluation failed: ${response.status}`);
  return response.json();
}

export async function getAccuracyCommandCenter(): Promise<AccuracyCommandCenter> {
  const response = await fetchWithTimeout("/api/accuracy-command-center");
  if (!response.ok) throw new ApiError(`Accuracy command center failed: ${response.status}`);
  return response.json();
}

// Workflow APIs
export async function getWorkflowStatus(): Promise<WorkflowStatus> {
  const res = await fetchWithTimeout("/api/workflows/status");
  if (!res.ok) throw new ApiError(`Workflow status failed: ${res.status}`);
  return res.json();
}

export async function triggerDailyOpen(params?: Record<string, unknown>): Promise<WorkflowTriggerResponse> {
  const res = await fetchWithTimeout("/api/workflows/daily-open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new ApiError(`Daily open failed: ${res.status}`);
  return res.json();
}

export async function triggerPreMatch(params?: Record<string, unknown>): Promise<WorkflowTriggerResponse> {
  const res = await fetchWithTimeout("/api/workflows/pre-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new ApiError(`Pre-match failed: ${res.status}`);
  return res.json();
}

export async function triggerPostMatch(params?: Record<string, unknown>): Promise<WorkflowTriggerResponse> {
  const res = await fetchWithTimeout("/api/workflows/post-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new ApiError(`Post-match failed: ${res.status}`);
  return res.json();
}

export async function triggerLock(params?: Record<string, unknown>): Promise<WorkflowTriggerResponse> {
  const res = await fetchWithTimeout("/api/workflows/lock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new ApiError(`Lock failed: ${res.status}`);
  return res.json();
}

export async function triggerFullWorkflow(params?: Record<string, unknown>): Promise<WorkflowTriggerResponse> {
  const res = await fetchWithTimeout("/api/workflows/full", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new ApiError(`Full workflow failed: ${res.status}`);
  return res.json();
}

export async function getWorkflowRuns(limit?: number): Promise<{ runs: WorkflowRunInfo[] }> {
  const url = `/api/workflows/runs${limit ? `?limit=${limit}` : ""}`;
  const res = await fetchWithTimeout(url);
  if (!res.ok) throw new ApiError(`Workflow runs failed: ${res.status}`);
  return res.json();
}

export async function getMatchCountBreakdown(): Promise<MatchCountBreakdown> {
  const res = await fetchWithTimeout("/api/match-count-breakdown");
  if (!res.ok) throw new ApiError(`Failed to fetch match count breakdown: ${res.status}`);
  return res.json();
}

export async function getKnockoutAudit(): Promise<KnockoutAudit> {
  const res = await fetchWithTimeout("/api/knockout-audit");
  if (!res.ok) throw new ApiError(`Failed to fetch knockout audit: ${res.status}`);
  return res.json();
}

export async function getErrorAttributionSummary(): Promise<ErrorAttributionSummary> {
  const res = await fetchWithTimeout("/api/error-attribution-summary");
  if (!res.ok) throw new ApiError(`Failed to fetch error attribution summary: ${res.status}`);
  const data = await res.json();
  // API returns { total_scored, counts: { ... }, rates: { ... } };
  // frontend expects counts fields at top level
  return data.counts ?? data;
}

export async function getDecisionSnapshotStatus(): Promise<DecisionSnapshotStatus> {
  const res = await fetchWithTimeout("/api/decision-snapshot-status");
  if (!res.ok) throw new ApiError(`Failed to fetch decision snapshot status: ${res.status}`);
  return res.json();
}

export async function getModelComparison(): Promise<{ comparison: ModelComparisonItem[]; sample_sufficient: boolean; sample_count: number }> {
  const res = await fetchWithTimeout("/api/model-comparison");
  if (!res.ok) throw new ApiError(`Failed to fetch model comparison: ${res.status}`);
  return res.json();
}

export async function getAdaptiveWeights(): Promise<{
  weights: Record<string, number>;
  performance: Record<string, { sample_count: number; effective_n?: number; brier: number | null; brier_var?: number; hit_rate: number | null; posterior_mu?: number; posterior_se?: number; ci_95?: [number, number] }>;
  is_adaptive: boolean;
  significance: Record<string, { diff_mean: number; t_stat: number; p_value: number; significant: boolean; better_source: string; n_pairs: number }>;
  last_updated: string;
  config: { algorithm: string; min_sample_size: number; max_weight_shift: number; hedge_eta: number; time_decay_half_life: number; significance_level: number; floor_weight: number; max_lookback: number };
}> {
  const res = await fetchWithTimeout("/api/adaptive-weights");
  if (!res.ok) throw new ApiError(`Failed to fetch adaptive weights: ${res.status}`);
  return res.json();
}
