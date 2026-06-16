import type {
  Dashboard, DecisionData, ModelScoreData,
  VersionScoreSummary, CalibrationBucket, MarketComparisonData,
  ModelRecommendation, DataQualityReport,
  AIModelStatus, AIPredictionItem, EnsemblePredictionItem,
  TeamProjection, BracketData, AIEvaluationResult,
  AccuracyCommandCenter,
  Match, ProfileEvaluation, TeamProfileEnvelope,
  MatchCountBreakdown, ErrorAttributionSummary,
  DecisionSnapshotStatus, ModelComparisonItem,
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

export async function getDashboard(): Promise<Dashboard> {
  const response = await fetchWithTimeout("/api/dashboard");
  if (!response.ok) throw new Error(`Dashboard request failed: ${response.status}`);
  return response.json();
}

export async function getMatchDetail(matchId: string): Promise<Match> {
  const response = await fetchWithTimeout(`/api/matches/${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new Error(`Match detail failed: ${response.status}`);
  return response.json();
}

export async function getTeamProfile(teamId: string): Promise<TeamProfileEnvelope> {
  const response = await fetchWithTimeout(`/api/team-profiles/${encodeURIComponent(teamId)}`);
  if (!response.ok) throw new Error(`Team profile failed: ${response.status}`);
  return response.json();
}

export async function getProfileEvaluation(): Promise<ProfileEvaluation> {
  const response = await fetchWithTimeout("/api/team-profiles/evaluation");
  if (!response.ok) throw new Error(`Profile evaluation failed: ${response.status}`);
  return response.json();
}

export async function getDecision(): Promise<DecisionData> {
  const response = await fetchWithTimeout("/api/decision");
  if (!response.ok) throw new Error(`Decision request failed: ${response.status}`);
  return response.json();
}

export async function getModelScore(): Promise<ModelScoreData> {
  const response = await fetchWithTimeout("/api/model-score");
  if (!response.ok) throw new Error(`Model score request failed: ${response.status}`);
  return response.json();
}

export async function refreshDashboard(): Promise<void> {
  const response = await fetchWithTimeout("/api/refresh", { method: "POST" });
  if (!response.ok) throw new Error(`Refresh failed: ${response.status}`);
}

// P2: Accuracy panel APIs
export async function getModelScoreByVersion(): Promise<{ versions: VersionScoreSummary[] }> {
  const response = await fetchWithTimeout("/api/model-score/by-version");
  if (!response.ok) throw new Error(`Model score by version failed: ${response.status}`);
  return response.json();
}

export async function getCalibration(): Promise<{ buckets: CalibrationBucket[] }> {
  const response = await fetchWithTimeout("/api/model-calibration");
  if (!response.ok) throw new Error(`Calibration request failed: ${response.status}`);
  return response.json();
}

export async function getMarketComparison(): Promise<MarketComparisonData> {
  const response = await fetchWithTimeout("/api/market-comparison");
  if (!response.ok) throw new Error(`Market comparison failed: ${response.status}`);
  return response.json();
}

export async function getModelRecommendation(): Promise<ModelRecommendation> {
  const response = await fetchWithTimeout("/api/model-recommendation");
  if (!response.ok) throw new Error(`Model recommendation failed: ${response.status}`);
  return response.json();
}

export async function getDataQuality(): Promise<DataQualityReport> {
  const response = await fetchWithTimeout("/api/data-quality");
  if (!response.ok) throw new Error(`Data quality check failed: ${response.status}`);
  return response.json();
}

// P2+: AI & Tournament APIs
export async function getAIModels(): Promise<{ enabled: boolean; models: AIModelStatus[] }> {
  const response = await fetchWithTimeout("/api/ai-models");
  if (!response.ok) throw new Error(`AI models request failed: ${response.status}`);
  return response.json();
}

export async function getAIPredictions(matchId: string): Promise<{ match_id: string; predictions: AIPredictionItem[] }> {
  const response = await fetchWithTimeout(`/api/ai-predictions?match_id=${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new Error(`AI predictions request failed: ${response.status}`);
  return response.json();
}

export async function runAIPrediction(matchId: string, modelVersion?: string): Promise<Record<string, unknown>> {
  let url = `/api/ai-predictions/run?match_id=${encodeURIComponent(matchId)}`;
  if (modelVersion && modelVersion !== "default") {
    url += `&model_version=${encodeURIComponent(modelVersion)}`;
  }
  const response = await fetchWithTimeout(url, { method: "POST", timeoutMs: 60_000 });
  if (!response.ok) throw new Error(`AI prediction run failed: ${response.status}`);
  return response.json();
}

export async function getEnsemble(matchId: string): Promise<{ match_id: string; predictions: EnsemblePredictionItem[] }> {
  const response = await fetchWithTimeout(`/api/ensemble?match_id=${encodeURIComponent(matchId)}`);
  if (!response.ok) throw new Error(`Ensemble request failed: ${response.status}`);
  return response.json();
}

export async function runEnsemble(matchId: string): Promise<Record<string, unknown>> {
  const response = await fetchWithTimeout(`/api/ensemble/run?match_id=${encodeURIComponent(matchId)}`, {
    method: "POST",
    timeoutMs: 60_000,
  });
  if (!response.ok) throw new Error(`Ensemble run failed: ${response.status}`);
  return response.json();
}

export async function getTournamentBracket(): Promise<BracketData> {
  const response = await fetchWithTimeout("/api/tournament/bracket");
  if (!response.ok) throw new Error(`Tournament bracket failed: ${response.status}`);
  return response.json();
}

export async function getTournamentProjections(): Promise<{ projections: TeamProjection[] }> {
  const response = await fetchWithTimeout("/api/tournament/projections");
  if (!response.ok) throw new Error(`Tournament projections failed: ${response.status}`);
  return response.json();
}

export async function getAIEvaluation(): Promise<AIEvaluationResult> {
  const response = await fetchWithTimeout("/api/ai-evaluation");
  if (!response.ok) throw new Error(`AI evaluation failed: ${response.status}`);
  return response.json();
}

export async function getAccuracyCommandCenter(): Promise<AccuracyCommandCenter> {
  const response = await fetchWithTimeout("/api/accuracy-command-center");
  if (!response.ok) throw new Error(`Accuracy command center failed: ${response.status}`);
  return response.json();
}

// Workflow APIs
export async function getWorkflowStatus(): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/status");
  if (!res.ok) throw new Error(`Workflow status failed: ${res.status}`);
  return res.json();
}

export async function triggerDailyOpen(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/daily-open", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Daily open failed: ${res.status}`);
  return res.json();
}

export async function triggerPreMatch(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/pre-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Pre-match failed: ${res.status}`);
  return res.json();
}

export async function triggerPostMatch(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/post-match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Post-match failed: ${res.status}`);
  return res.json();
}

export async function triggerLock(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/lock", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Lock failed: ${res.status}`);
  return res.json();
}

export async function triggerUpdatePredictions(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/update-predictions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Update predictions failed: ${res.status}`);
  return res.json();
}

export async function triggerFullWorkflow(params?: Record<string, unknown>): Promise<Record<string, unknown>> {
  const res = await fetchWithTimeout("/api/workflows/full", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params || {}),
    timeoutMs: 120_000,
  });
  if (!res.ok) throw new Error(`Full workflow failed: ${res.status}`);
  return res.json();
}

export async function getWorkflowRuns(limit?: number): Promise<Record<string, unknown>> {
  const url = `/api/workflows/runs${limit ? `?limit=${limit}` : ""}`;
  const res = await fetchWithTimeout(url);
  if (!res.ok) throw new Error(`Workflow runs failed: ${res.status}`);
  return res.json();
}

export async function getMatchCountBreakdown(): Promise<MatchCountBreakdown> {
  const res = await fetchWithTimeout("/api/match-count-breakdown");
  if (!res.ok) throw new Error("Failed to fetch match count breakdown");
  return res.json();
}

export async function getErrorAttributionSummary(): Promise<ErrorAttributionSummary> {
  const res = await fetchWithTimeout("/api/error-attribution-summary");
  if (!res.ok) throw new Error("Failed to fetch error attribution summary");
  return res.json();
}

export async function getDecisionSnapshotStatus(): Promise<DecisionSnapshotStatus> {
  const res = await fetchWithTimeout("/api/decision-snapshot-status");
  if (!res.ok) throw new Error("Failed to fetch decision snapshot status");
  return res.json();
}

export async function getModelComparison(): Promise<{ comparison: ModelComparisonItem[]; sample_sufficient: boolean; sample_count: number }> {
  const res = await fetchWithTimeout("/api/model-comparison");
  if (!res.ok) throw new Error("Failed to fetch model comparison");
  return res.json();
}
