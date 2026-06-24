export type Probability = {
  first: number; second: number; third: number; fourth: number;
  qualify: number; standard_error: number;
};

export type Standing = {
  position: number; played: number; won: number; drawn: number; lost: number;
  goals_for: number; goals_against: number; goal_difference: number; points: number;
  tiebreak_uncertain: boolean;
};

export type Team = {
  id: string; name: string; short_name: string; code: string; flag: string | null;
  elo: number; fifa_rank: number | null; fifa_points: number | null; recent_form: string;
  standing: Standing; qualification: Probability;
};

export type TeamRef = Pick<Team, "id" | "name" | "short_name" | "flag">;

export type Scoreline = { home_goals: number; away_goals: number; probability: number };

export type ManualAdjustment = {
  id: number; match_id: string; adjustment_type: string;
  affected_team_id: string; affected_team_name: string;
  attack_delta: number; defense_delta: number; confidence: string;
  note: string; created_by: string; created_at: string;
};

export type MatchIntelligence = {
  type: string; provider: string; confidence: number;
  fetched_at: string; payload: Record<string, unknown>;
};

export type AutoAdjustment = {
  type: string; affected_team_id: string; confidence_penalty?: number;
  reason: string; source_intelligence_ids?: number[];
};

export type NumericalAdjustment = {
  type: string; affected_team_id: string; attack_delta: number; defense_delta: number; reason: string;
};

export type NumericalDeltaSummary = {
  home_attack_delta: number; home_defense_delta: number;
  away_attack_delta: number; away_defense_delta: number;
};

export type SnapshotStatus = {
  locked: boolean; locked_at: string | null; is_fallback: boolean;
  participates_in_model_score: boolean; real_time_only: boolean;
};

/** Per-source review data for a finished match */
export type MatchReviewSource = {
  predicted_result: string;
  outcome_hit: boolean;
  brier: number;
  actual_probability: number;
  probabilities: { home_win: number; draw: number; away_win: number };
  deviations: { home_win: number; draw: number; away_win: number };
  model_version?: string;
};

/** Review data for a finished match (P0-2) */
export type MatchReview = {
  actual_result: string;
  actual_score: { home: number; away: number };
  winner_hit: boolean | null;
  best_model: string | null;
  baseline: MatchReviewSource | null;
  ai: MatchReviewSource | null;
  ensemble: MatchReviewSource | null;
  market: MatchReviewSource | null;
};

/** AI prediction summary for MatchCard (P0-3) */
export type AIPredictionSummary = {
  home_win: number; draw: number; away_win: number;
  model_version: string; recommended_label: string | null;
};

/** Ensemble prediction summary for MatchCard (P0-3) */
export type EnsemblePredictionSummary = {
  home_win: number; draw: number; away_win: number;
  model_version: string; system_weight: number; market_weight: number;
};

export type Prediction = {
  home_xg: number; away_xg: number; home_win: number; draw: number; away_win: number;
  base_home_win?: number; base_draw?: number; base_away_win?: number;
  scorelines: Scoreline[]; confidence: number; confidence_label: string;
  data_confidence: number | null; data_confidence_label: string | null;
  model_confidence: number | null; model_confidence_label: string | null;
  explanation: string; model_inputs: Record<string, unknown>; model_version: string;
};

export type Divergence = {
  home_diff: number; draw_diff: number; away_diff: number;
  max_divergence: number; level: string;
};

export type MarketData = {
  home_probability: number; draw_probability: number; away_probability: number;
  raw_overround: number; divergence: Divergence | null;
};

export type Match = {
  id: string; group_code: string; kickoff: string; venue: string | null; status: string;
  home_team: TeamRef; away_team: TeamRef; home_score: number | null; away_score: number | null;
  manual_adjustments: ManualAdjustment[];
  intelligence: MatchIntelligence[];
  auto_adjustments: AutoAdjustment[];
  numerical_adjustments?: NumericalAdjustment[];
  numerical_delta_summary?: NumericalDeltaSummary;
  numerical_enabled?: boolean;
  risk_flags: string[];
  snapshot_status: SnapshotStatus;
  prediction: Prediction | null; market: MarketData | null; source: string; source_updated_at: string | null;
  result_source?: string | null;
  result_synced_at?: string | null;
  revision_id?: number;
  team_profiles?: MatchTeamProfiles;
  profile_prediction?: ProfilePrediction | null;
  match_review?: MatchReview | null;
  ai_prediction?: AIPredictionSummary | null;
  ensemble_prediction?: EnsemblePredictionSummary | null;
};

export type TeamProfile = {
  team_id: string; team_code: string; profile_version: string; profile_as_of: string;
  sample_count: number; world_cup_sample_count: number; qualifier_sample_count: number;
  goal_for_avg: number; goal_against_avg: number; draw_rate_overall: number;
  draw_rate_vs_elite: number; draw_rate_vs_strong: number; draw_resilience_score: number;
  favorite_win_rate: number; favorite_fail_to_win_rate: number; favorite_overconfidence_risk: number;
  underdog_win_or_draw_rate: number; upset_potential_score: number; defensive_resilience_score: number;
  world_cup_experience_score: number; opening_match_slow_start_score: number;
  low_score_tendency: number; high_score_tendency: number;
  traits_json: string[]; tier_stats_json: Record<string, { sample_count: number; win_rate: number; draw_rate: number; loss_rate: number; goal_for_avg: number; goal_against_avg: number }>;
  source_summary_json?: { mode: string; sources: string[] };
  long_term_strength_score: number | null; recent_form_score: number | null; attack_score: number | null; defense_score: number | null;
  stability_score: number | null; tournament_experience_score: number | null; data_quality_score: number;
  lineup_integrity_score: number | null; injury_risk_score: number | null; rest_days: number | null;
  schedule_fatigue_score: number | null; environment_adaptation_score: number | null;
  tactical_style_tags: string[]; strengths: string[]; weaknesses: string[]; risk_flags: string[];
  missing_fields: string[]; source_list: string[];
  usage_scope: string; prediction_enabled: boolean;
  team_profile_narrative: Record<string, string>;
  team_profile_data_quality: { quality_label?: string; contains_mock?: boolean; missing_fields?: string[]; source_list?: string[]; usage_scope?: string; prediction_enabled?: boolean; updated_at?: string };
  profile_modules_json: Record<string, any>;
  lineup_integrity_status?: string; environment_adaptation_status?: string;
};

export type TeamProfileEnvelope = { profile: TeamProfile; summary: string };
export type MatchTeamProfiles = { home: TeamProfileEnvelope | null; away: TeamProfileEnvelope | null };
export type ProfilePrediction = {
  model_version: string; profile_version: string; profile_as_of: string;
  home_win: number; draw: number; away_win: number; home_xg: number; away_xg: number;
  probability_deltas: Record<string, number>; xg_deltas: Record<string, number>;
  risk_flags: string[]; triggered_traits: string[]; explanation: string; is_pre_match_locked: boolean;
};

export type ProfileEvaluation = {
  model_version: string; sample_count: number; baseline_brier: number | null; profile_brier: number | null;
  helped: number; hurt: number; neutral: number;
  most_helpful_traits: Array<{ trait: string; count: number }>;
  most_misleading_traits: Array<{ trait: string; count: number }>;
  matches: Array<{ match_id: string; baseline_brier: number; profile_brier: number; brier_delta: number; effect: string; traits: string[]; risk_flags: string[]; explanation: string }>;
};

export type Group = { code: string; name: string; teams: Team[]; matches: Match[] };

export type DataSource = {
  provider: string; source_url?: string; fetched_at?: string; status: string;
  coverage?: Record<string, number>; error: string | null;
  daily_limit?: number; used_today?: number; last_success_at?: string | null;
};

export type Dashboard = {
  revision: { id: number; created_at: string; model_version: string; simulation_iterations: number; simulation_seed: number };
  groups: Group[];
  data_sources: DataSource[];
};

export type DecisionPrediction = {
  home_win: number; draw: number; away_win: number;
  base_home_win?: number; base_draw?: number; base_away_win?: number;
  confidence_label: string; model_confidence_label: string | null;
  home_xg: number; away_xg: number;
};

export type DecisionMarket = {
  home_probability: number; draw_probability: number; away_probability: number;
  divergence: { max_divergence: number; level: string } | null;
};

export type DecisionMatch = {
  id: string; group_code: string; kickoff: string;
  home_team: TeamRef; away_team: TeamRef;
  status: string; home_score: number | null; away_score: number | null;
  manual_adjustments: ManualAdjustment[];
  intelligence_risks?: { type: string; affected_team_id: string; reason: string; }[];
  numerical_adjustments?: NumericalAdjustment[];
  numerical_enabled?: boolean;
  prediction?: DecisionPrediction; market?: DecisionMarket;
};

export type ReviewMatch = DecisionMatch & {
  snapshot?: { home_win: number; draw: number; away_win: number; outcome_correct: boolean };
  review?: { brier: number; log_loss: number; xg_error: number; bias_explanation: string };
};

export type DecisionIntelligenceRisk = {
  match_id: string; home_team: TeamRef; away_team: TeamRef; kickoff: string;
  risk_type: string; level: string; provider: string; reason: string;
};

export type DecisionData = {
  review_summary: {
    matches_scored: number; brier_score: number; log_loss: number;
    outcome_hit_rate: number; top_score_hit_rate: number; xg_mae: number;
  };
  today_matches: DecisionMatch[];
  most_confident: DecisionMatch[];
  most_uncertain: DecisionMatch[];
  biggest_divergence: DecisionMatch[];
  upset_risk: DecisionMatch[];
  recent_review: ReviewMatch[];
  intelligence_risks: DecisionIntelligenceRisk[];
};

export type ModelScoreHistoryEntry = {
  id: number; revision_id: number; model_version: string; matches_scored: number;
  brier_score: number; log_loss: number; outcome_hit_rate: number;
  top_score_hit_rate: number; xg_mae: number; per_match: unknown[]; created_at: string;
};

export type ModelVersionSummary = {
  model_version: string; runs: number; total_matches_scored: number;
  average_brier_score: number; average_log_loss: number; average_outcome_hit_rate: number;
  average_top_score_hit_rate: number; average_xg_mae: number; latest: ModelScoreHistoryEntry;
};

export type ModelScoreData = ModelScoreHistoryEntry & {
  history: ModelScoreHistoryEntry[];
  model_versions: ModelVersionSummary[];
  comparison: {
    current_version: ModelVersionSummary;
    previous_version: ModelVersionSummary;
    deltas: {
      brier_score: number; log_loss: number; outcome_hit_rate: number;
      top_score_hit_rate: number; xg_mae: number;
    };
  } | null;
};

// P2: Accuracy panel types
export type MatchScoreDetailItem = {
  match_id: string; kickoff: string; home_team: string; away_team: string;
  model_version: string; locked_at: string;
  home_win_prob: number; draw_prob: number; away_win_prob: number;
  max_prob: number; actual_result: string; outcome_hit: boolean;
  brier: number; logloss: number; xg_error: number;
  error_types: string[]; error_reasons: string[]; suggested_fixes: string[];
  warning_effect: string; numerical_effect: string; probability_effect: number;
  market_home_prob: number | null; market_draw_prob: number | null; market_away_prob: number | null;
};

export type VersionScoreSummary = {
  model_version: string; sample_count: number; hit_rate: number;
  brier: number; logloss: number; avg_confidence: number;
  upset_miss_count: number; draw_miss_count: number;
  favorite_overestimated_count: number; underdog_underestimated_count: number;
  overconfident_wrong_count: number;
  warning_helped_count: number; warning_hurt_count: number;
  numerical_helped_count: number; numerical_hurt_count: number;
};

export type CalibrationBucket = {
  label: string; sample_count: number;
  predicted_avg_prob: number; actual_win_rate: number;
  calibration_gap: number; note: string;
};

export type MarketComparisonData = {
  market_sample_count: number;
  model_brier: number; market_brier: number; blended_brier: number;
  model_logloss: number; market_logloss: number; blended_logloss: number;
  suggested_market_blend_weight: number;
  market_helped_count: number; market_hurt_count: number; market_neutral_count: number;
};

export type ModelRecommendation = {
  recommended_model_version: string; reason: string; confidence: string;
  sample_warning: string; fallback_model_version: string;
  best_brier?: number; baseline_brier?: number;
  brier_improvement?: number; relative_improvement?: number;
};

export type DataQualityCheck = {
  check: string; status: string; count: number;
  details?: (string | Record<string, unknown>)[]; note?: string; ratio?: number;
};

export type DataQualityReport = {
  timestamp: string;
  summary: { total_checks: number; pass: number; warn: number; fail: number; overall_status: string };
  checks: DataQualityCheck[];
};

// P2+: AI & Tournament types
export type AIModelStatus = {
  provider: string; model_id: string; model_version: string;
  display_name: string; enabled: boolean; configured: boolean;
  cost_tier: string; latency_tier: string; role: string;
  prompt_version?: string;
  status: "ready" | "disabled" | "disabled_no_key" | "error" | "unconfigured";
  last_success_at: string | null; last_error_at: string | null;
  provider_health?: { available: boolean; error: string | null; last_check: string | null };
};

export type AIPredictionItem = {
  id: number; match_id: string; provider: string; model_id: string;
  model_version: string; prompt_version: string;
  parsed_home_win: number | null; parsed_draw: number | null; parsed_away_win: number | null;
  confidence: number | null; risk_flags: string[]; key_factors: string[];
  reason: string; uncertainties: string[];
  disagreement_with_system: string; disagreement_with_market: string;
  recommended_label: string;
  created_at: string; locked_at: string | null;
  is_pre_match_locked: boolean; is_fallback_locked: boolean;
  real_time_only: boolean; error_code: string | null;
  error_message: string | null; latency_ms: number | null;
  identical_to_baseline?: boolean; deviation_from_baseline?: number | null;
};

export type EnsemblePredictionItem = {
  id: number; match_id: string; model_version: string;
  system_model_version: string; system_weight: number; market_weight: number;
  ai_weights: Record<string, number>;
  source_probabilities: Record<string, { version?: string; probs: Record<string, number>; weight: number }>;
  home_win: number; draw: number; away_win: number;
  confidence: number; reason: string;
  created_at: string; locked_at: string | null;
  is_pre_match_locked: boolean;
  source_status: Record<string, unknown>;
};

export type TeamProjection = {
  team_id: string; group_qualify: number;
  round_of_32: number; round_of_16: number;
  quarter_final: number; semi_final: number;
  final: number; champion: number;
};

export type BracketMatchup = {
  match_position: number; stage: string;
  home_source: string; away_source: string;
  home_team: { team_id: string; team_name: string } | null;
  away_team: { team_id: string; team_name: string } | null;
};

export type BracketData = {
  round_of_32: BracketMatchup[];
  round_of_16: BracketMatchup[];
  quarter_final: BracketMatchup[];
  semi_final: BracketMatchup[];
  third_place: BracketMatchup[];
  final: BracketMatchup[];
};

export type AIEvaluationResult = {
  system: { sample_count: number; brier: number | null; logloss: number | null; hit_rate: number | null };
  ai_by_version: Record<string, { sample_count: number; brier: number | null; logloss: number | null; hit_rate: number | null; helped: number; hurt: number }>;
  ensemble: { sample_count: number; brier: number | null; logloss: number | null; hit_rate: number | null; helped: number; hurt: number };
  ai_effect: Record<string, { effect: string; brier_diff: number }>;
};

// Model comparison item for Baseline vs AI v1 vs AI v2 vs Ensemble
export type ModelComparisonItem = {
  source: string;
  model_version: string;
  prompt_version: string | null;
  role: "production" | "shadow" | "unknown";
  sample_count: number;
  brier: number | null;
  logloss: number | null;
  hit_rate: number | null;
  available: boolean;
};

// P2+: Accuracy Command Center
export type AccuracyCommandCenter = {
  sample_count: number;
  baseline_score: { available: boolean; sample_count?: number; brier?: number | null; logloss?: number | null; hit_rate?: number | null };
  model_recommendation: ModelRecommendation;
  version_scores: VersionScoreSummary[];
  model_comparison?: ModelComparisonItem[];
  calibration: { buckets: CalibrationBucket[] };
  market_comparison: MarketComparisonData;
  data_quality: DataQualityReport;
  ai_evaluation: AIEvaluationResult;
  ai_models: { enabled: boolean; models: AIModelStatus[] };
  scoring_exclusions: Array<{ match_id: string; home_team: string; away_team: string; reason: string }>;
};

// Match count breakdown
export interface MatchCountBreakdown {
  total_finished: number;
  has_pre_match_prediction: number;
  has_pre_kickoff_snapshot: number;
  has_locked_snapshot: number;
  has_fallback_snapshot: number;
  actually_scored: number;
  missing_snapshot: number;
  details: MatchCountDetail[];
}

export interface MatchCountDetail {
  match_id: string;
  home_team: string;
  away_team: string;
  status: "scored" | "no_pre_match_snapshot" | "no_locked_snapshot" | "no_prediction" | "no_final_score" | "excluded_after_kickoff" | "ai_missing" | "ensemble_missing";
  status_label: string;
}

// Error attribution summary
export interface ErrorAttributionSummary {
  draw_underestimated: number;
  favorite_overestimated: number;
  underdog_underestimated: number;
  overconfident_wrong: number;
  low_score_draw_missed: number;
  market_missing: number;
  ai_missing: number;
  ensemble_helped: number;
  ensemble_hurt: number;
}

// Shadow model comparison row
export interface ShadowModelRow {
  model_version: string;
  label: string;
  sample_count: number;
  hit_rate: number;
  brier: number;
  log_loss: number;
  draw_hit: number;
  draw_miss: number;
  favorite_wrong: number;
  overconfident_wrong: number;
  recommendation: "observe" | "trend" | "consider_switch";
}

// Decision snapshot status
export interface DecisionSnapshotStatus {
  status: "ready" | "partial" | "none";
  matches_total: number;
  snapshots_ready: number;
  missing: number;
  last_snapshot_at: string | null;
  rule: string;
}

// Workflow types
export interface WorkflowStepInfo {
  step_name: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  summary: Record<string, unknown> | null;
  error_message: string | null;
}

export interface WorkflowProgress {
  total_steps: number;
  completed_steps: number;
  percent: number;
  running_step: string | null;
  failed_steps: Array<{ step_name: string; error_message: string | null }>;
}

export interface WorkflowRunInfo {
  id: number;
  workflow_type: string;
  trigger_source: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  duration_seconds: number | null;
  steps: WorkflowStepInfo[];
  summary: Record<string, unknown> | null;
  error_message: string | null;
  progress?: WorkflowProgress | null;
}

export interface WorkflowTriggerResponse {
  status: string;
  message?: string;
  run_id?: number;
  progress?: WorkflowProgress;
}

export interface ButtonState {
  enabled: boolean;
  reason: string;
  estimated_calls?: number;
  needs_ai?: number;
}

export interface WorkflowStatus {
  today_status: string;
  last_run_at: string | null;
  recommended_action: string;
  yesterday_matches: {
    count: number;
    scored: number;
    needs_review: boolean;
  };
  upcoming_matches: {
    count_24h: number;
    count_48h: number;
    baseline_ready: number;
    ai_ready: number;
    ensemble_ready: number;
    needs_ai: number;
  };
  lock_status: {
    matches_near_kickoff: number;
    locked: number;
    needs_lock: number;
    real_time_only: number;
  };
  last_run: WorkflowRunInfo | null;
  ai_stats: {
    today_ai_calls: number;
    today_ai_failed: number;
    today_ai_skipped: number;
    cooldown_skipped: boolean;
    only_missing_skipped: number;
  };
  ai_status: {
    configured_models: number;
    attempted: number;
    success: number;
    failed: number;
    parse_error: number;
    effective_for_ensemble: number;
    effective_for_scoring: number;
    api_key_ready: boolean;
  };
  next_action: {
    message: string;
    action: string;
  };
  decision_snapshot_status: {
    status: string;
    matches_total: number;
    snapshots_ready: number;
    missing: number;
    last_snapshot_at: string | null;
    rule: string;
  };
  button_states: {
    daily_open: ButtonState;
    pre_match: ButtonState;
    ai_prediction: ButtonState;
    post_match: ButtonState;
    lock: ButtonState;
    full: ButtonState;
  };
}
