import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import App from "../App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});


const team = (group: string, index: number) => ({
  id: `${group}${index}`,
  name: `Team ${group}${index}`,
  short_name: `Team ${group}${index}`,
  code: `${group}${index}`,
  flag: "⚽",
  elo: 1700 - index,
  fifa_rank: null,
  fifa_points: null,
  recent_form: "WDLDW",
  standing: { position: index, played: 0, won: 0, drawn: 0, lost: 0, goals_for: 0, goals_against: 0, goal_difference: 0, points: 0, tiebreak_uncertain: false },
  qualification: { first: .25, second: .25, third: .25, fourth: .25, qualify: .66, standard_error: .01 },
});

const dashboard = {
  revision: { id: 1, created_at: "2026-06-13T00:00:00Z", model_version: "elo-poisson-v1", simulation_iterations: 50000, simulation_seed: 7 },
  data_sources: [{ provider: "openfootball", source_url: "https://example.com", fetched_at: "2026-06-13T00:00:00Z", status: "available", coverage: { teams: 48, matches: 72 }, error: null }],
  groups: [..."ABCDEFGHIJKL"].map((group) => ({
    code: group,
    name: `Group ${group}`,
    teams: [1, 2, 3, 4].map((index) => team(group, index)),
    matches: Array.from({ length: 6 }, (_, index) => ({
      id: `${group}-${index}`,
      group_code: group,
      kickoff: "2026-06-13T10:00:00Z",
      venue: "Test Stadium",
      status: "scheduled",
      home_team: { id: `${group}1`, name: `Team ${group}1`, short_name: `Team ${group}1`, flag: "⚽" },
      away_team: { id: `${group}2`, name: `Team ${group}2`, short_name: `Team ${group}2`, flag: "⚽" },
      home_score: null,
      away_score: null,
      manual_adjustments: index === 0 ? [{
        id: 1,
        match_id: `${group}-${index}`,
        adjustment_type: "伤停",
        affected_team_id: `${group}1`,
        affected_team_name: `Team ${group}1`,
        attack_delta: -0.12,
        defense_delta: 0,
        confidence: "medium",
        note: "主力前锋伤缺，进攻下调。",
        created_by: "manual",
        created_at: "2026-06-13T00:00:00Z",
      }] : [],
      source: "openfootball",
      source_updated_at: "2026-06-13T00:00:00Z",
      market: index === 0 ? {
        home_probability: .34,
        draw_probability: .28,
        away_probability: .38,
        raw_overround: 1.04,
        divergence: { home_diff: .11, draw_diff: .02, away_diff: -.13, max_divergence: .13, level: "高" },
      } : null,
      prediction: { home_xg: 1.4, away_xg: 1.0, home_win: .45, draw: .3, away_win: .25, scorelines: [{ home_goals: 1, away_goals: 0, probability: .12 }], confidence: .8, confidence_label: "高", data_confidence: .85, data_confidence_label: "高", model_confidence: .22, model_confidence_label: "中", explanation: "Model explanation", model_inputs: { home_elo: 1700, away_elo: 1600 }, model_version: "elo-poisson-v1" },
    })),
  })),
};

function dashboardWithFinishedAiOnlyReview() {
  const copy = JSON.parse(JSON.stringify(dashboard));
  copy.groups[0].matches[0] = {
    ...copy.groups[0].matches[0],
    id: "A-final-ai-only",
    kickoff: "2026-06-24T10:00:00Z",
    status: "final",
    home_score: 2,
    away_score: 1,
    prediction: null,
    snapshot_status: {
      locked: false,
      locked_at: null,
      is_fallback: false,
      participates_in_model_score: false,
      real_time_only: false,
    },
    ai_prediction: {
      home_win: 0.48,
      draw: 0.27,
      away_win: 0.25,
      model_version: "ai-deepseek-v4-flash-v1",
      recommended_label: "主胜",
    },
    ensemble_prediction: {
      home_win: 0.52,
      draw: 0.25,
      away_win: 0.23,
      model_version: "ensemble-v1",
      system_weight: 0.45,
      market_weight: 0.25,
    },
  };
  return copy;
}

const mockTeamProfile = () => ({
  team_id: "A1",
  team_code: "A1",
  profile_version: "team-profile-v1",
  profile_as_of: "2026-06-19T00:00:00Z",
  sample_count: 16,
  world_cup_sample_count: 10,
  qualifier_sample_count: 6,
  goal_for_avg: 1.6,
  goal_against_avg: 0.8,
  draw_rate_overall: 0.25,
  draw_rate_vs_elite: 0.2,
  draw_rate_vs_strong: 0.3,
  draw_resilience_score: 0.4,
  favorite_win_rate: 0.7,
  favorite_fail_to_win_rate: 0.3,
  favorite_overconfidence_risk: 0.15,
  underdog_win_or_draw_rate: 0.5,
  upset_potential_score: 0.4,
  defensive_resilience_score: 0.7,
  world_cup_experience_score: 0.6,
  opening_match_slow_start_score: 0.2,
  low_score_tendency: 0.3,
  high_score_tendency: 0.2,
  traits_json: ["防守优先", "大赛经验丰富"],
  tier_stats_json: {},
  source_summary_json: { mode: "historical_real", sources: ["historical_real"] },
  long_term_strength_score: 76,
  recent_form_score: 68,
  attack_score: 62,
  defense_score: 78,
  stability_score: 70,
  tournament_experience_score: 72,
  data_quality_score: 84,
  lineup_integrity_score: null,
  injury_risk_score: null,
  rest_days: null,
  schedule_fatigue_score: null,
  environment_adaptation_score: null,
  tactical_style_tags: ["防守反击型"],
  strengths: ["防守稳定性较高"],
  weaknesses: [],
  risk_flags: [],
  missing_fields: ["lineup_integrity_score", "injury_risk_score", "rest_days"],
  source_list: ["historical_real"],
  usage_scope: "display_only",
  prediction_enabled: false,
  team_profile_narrative: { headline: "长期实力评级：B。", data_quality: "仅展示。" },
  team_profile_data_quality: { quality_label: "high", contains_mock: false, source_list: ["historical_real"], missing_fields: ["lineup_integrity_score"], usage_scope: "display_only", prediction_enabled: false, updated_at: "2026-06-19T00:00:00Z" },
  profile_modules_json: {
    long_term_strength: { grade: "B", elo: 1700, two_year_record: { wins: 8, draws: 4, losses: 4, goal_difference: 8 } },
    recent_form: { recent_5: { wins: 3, draws: 1, losses: 1 }, recent_5_goal_for_avg: 1.4, recent_5_goal_against_avg: 0.8, unbeaten_streak: 3 },
    attack_defense: { attack_level: "medium", defense_level: "high", tempo_tendency: "均衡", clean_sheet_rate: 0.5 },
    tactical_style: { tags: ["防守反击型"] },
    lineup_players: { status: "unavailable", note: "No verified lineup feed." },
    environment: { status: "unavailable" },
    data_quality: { quality_label: "high" },
  },
  lineup_integrity_status: "unavailable",
  environment_adaptation_status: "unavailable",
});

const decision = {
  review_summary: { matches_scored: 1, brier_score: .24, log_loss: .51, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .35 },
  today_matches: [], most_confident: [], most_uncertain: [], biggest_divergence: [], upset_risk: [],
  recent_review: [{
    id: "A-final", group_code: "A", kickoff: "2026-06-12T10:00:00Z", status: "final",
    home_team: { id: "A1", name: "Team A1", short_name: "Team A1", flag: "⚽" },
    away_team: { id: "A2", name: "Team A2", short_name: "Team A2", flag: "⚽" },
    home_score: 2, away_score: 0,
    manual_adjustments: [],
    prediction: { home_win: .6, draw: .25, away_win: .15, confidence_label: "高", model_confidence_label: "中", home_xg: 1.5, away_xg: .8 },
    snapshot: { home_win: .6, draw: .25, away_win: .15, outcome_correct: true },
    review: { brier: .24, log_loss: .51, xg_error: .35, bias_explanation: "模型较准确地识别了主胜方向，但低估了净胜优势。" },
  }],
};

const modelScore = {
  id: 2,
  revision_id: 2,
  model_version: "elo-poisson-v1.1",
  matches_scored: 1,
  brier_score: .22,
  log_loss: .49,
  outcome_hit_rate: 1,
  top_score_hit_rate: 0,
  xg_mae: .31,
  per_match: [],
  created_at: "2026-06-13T01:00:00Z",
  history: [
    { id: 2, revision_id: 2, model_version: "elo-poisson-v1.1", matches_scored: 1, brier_score: .22, log_loss: .49, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .31, per_match: [], created_at: "2026-06-13T01:00:00Z" },
    { id: 1, revision_id: 1, model_version: "elo-poisson-v1", matches_scored: 1, brier_score: .24, log_loss: .51, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .35, per_match: [], created_at: "2026-06-12T01:00:00Z" },
  ],
  model_versions: [
    {
      model_version: "elo-poisson-v1.1",
      runs: 1,
      total_matches_scored: 1,
      average_brier_score: .22,
      average_log_loss: .49,
      average_outcome_hit_rate: 1,
      average_top_score_hit_rate: 0,
      average_xg_mae: .31,
      latest: { id: 2, revision_id: 2, model_version: "elo-poisson-v1.1", matches_scored: 1, brier_score: .22, log_loss: .49, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .31, per_match: [], created_at: "2026-06-13T01:00:00Z" },
    },
    {
      model_version: "elo-poisson-v1",
      runs: 1,
      total_matches_scored: 1,
      average_brier_score: .24,
      average_log_loss: .51,
      average_outcome_hit_rate: 1,
      average_top_score_hit_rate: 0,
      average_xg_mae: .35,
      latest: { id: 1, revision_id: 1, model_version: "elo-poisson-v1", matches_scored: 1, brier_score: .24, log_loss: .51, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .35, per_match: [], created_at: "2026-06-12T01:00:00Z" },
    },
  ],
  comparison: {
    current_version: {
      model_version: "elo-poisson-v1.1",
      runs: 1,
      total_matches_scored: 1,
      average_brier_score: .22,
      average_log_loss: .49,
      average_outcome_hit_rate: 1,
      average_top_score_hit_rate: 0,
      average_xg_mae: .31,
      latest: { id: 2, revision_id: 2, model_version: "elo-poisson-v1.1", matches_scored: 1, brier_score: .22, log_loss: .49, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .31, per_match: [], created_at: "2026-06-13T01:00:00Z" },
    },
    previous_version: {
      model_version: "elo-poisson-v1",
      runs: 1,
      total_matches_scored: 1,
      average_brier_score: .24,
      average_log_loss: .51,
      average_outcome_hit_rate: 1,
      average_top_score_hit_rate: 0,
      average_xg_mae: .35,
      latest: { id: 1, revision_id: 1, model_version: "elo-poisson-v1", matches_scored: 1, brier_score: .24, log_loss: .51, outcome_hit_rate: 1, top_score_hit_rate: 0, xg_mae: .35, per_match: [], created_at: "2026-06-12T01:00:00Z" },
    },
    deltas: { brier_score: -.02, log_loss: -.02, outcome_hit_rate: 0, top_score_hit_rate: 0, xg_mae: -.04 },
  },
};

function workflowStatus(overrides: Record<string, unknown> = {}) {
  return {
    today_status: "completed",
    last_run_at: "2026-06-13T08:00:00Z",
    recommended_action: null,
    button_states: {},
    yesterday_matches: { count: 0, scored: 0, needs_review: false },
    upcoming_matches: { count_24h: 0, count_48h: 0, baseline_ready: 0, ai_ready: 0, ensemble_ready: 0, needs_ai: 0 },
    lock_status: { matches_near_kickoff: 0, locked: 0, needs_lock: 0, real_time_only: 0 },
    ai_stats: { today_ai_calls: 0, today_ai_failed: 0, today_ai_skipped: 0, cooldown_skipped: false, only_missing_skipped: 0 },
    ...overrides,
  };
}

function renderApp(dashboardPayload = dashboard, workflowStatusPayload = workflowStatus()) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    const path = new URL(url, "http://localhost").pathname;
    if (path === "/api/model-score/details") return { ok: true, json: async () => ({ details: [], exclusions: [] }) };
    if (path === "/api/model-score/by-version") return { ok: true, json: async () => ({ versions: [] }) };
    if (path === "/api/model-score") return { ok: true, json: async () => modelScore };
    if (path === "/api/decision") return { ok: true, json: async () => decision };
    if (path === "/api/accuracy-command-center") return { ok: true, json: async () => ({
      model_recommendation: null, version_scores: [], calibration: { buckets: [] },
      market_comparison: { market_sample_count: 0, model_brier: 0, market_brier: 0, blended_brier: 0, model_logloss: 0, market_logloss: 0, blended_logloss: 0, suggested_market_blend_weight: 0, market_helped_count: 0, market_hurt_count: 0, market_neutral_count: 0 },
      data_quality: null, ai_evaluation: { system: { sample_count: 0, brier: null, logloss: null, hit_rate: null }, ai_by_version: {}, ensemble: { sample_count: 0, brier: null, logloss: null, hit_rate: null, helped: 0, hurt: 0 }, ai_effect: {} },
      ai_models: { enabled: false, models: [] },
    }) };
    if (path === "/api/workflows/status") return { ok: true, json: async () => workflowStatusPayload };
    if (path === "/api/workflows/runs") return { ok: true, json: async () => ({ runs: [] }) };
    if (path === "/api/adaptive-weights") return { ok: true, json: async () => ({
      weights: { system: 0.35, market: 0.30, "ai_ai-test-v1": 0.35 },
      performance: { system: { sample_count: 3, effective_n: 2.5, brier: 0.4, brier_var: 0.02, hit_rate: 0.8, posterior_mu: 0.42, posterior_se: 0.08, ci_95: [0.26, 0.58] }, market: { sample_count: 3, effective_n: 2.5, brier: 0.45, brier_var: 0.03, hit_rate: 0.7, posterior_mu: 0.44, posterior_se: 0.09, ci_95: [0.26, 0.62] } },
      is_adaptive: false, significance: {},
      last_updated: "2026-06-13T08:00:00Z",
      config: { algorithm: "bayesian_model_averaging_v2", min_sample_size: 10, max_weight_shift: 0.12, hedge_eta: 1.5, time_decay_half_life: 20, significance_level: 0.10, floor_weight: 0.05, max_lookback: 60 },
    }) };
    if (path === "/api/tournament/projections") return { ok: true, json: async () => ({ teams: [], source: "simulation" }) };
    if (path === "/api/tournament/bracket") return { ok: true, json: async () => ({ rounds: [] }) };
    if (path === "/api/ai-models") return { ok: true, json: async () => ({ enabled: false, models: [] }) };
    if (path === "/api/ai-predictions") return { ok: true, json: async () => ({ match_id: "", predictions: [] }) };
    if (path === "/api/ensemble") return { ok: true, json: async () => ({ match_id: "", predictions: [] }) };
    if (path === "/api/ai-evaluation") return { ok: true, json: async () => ({ system: { sample_count: 0 }, ai_by_version: {}, ensemble: { sample_count: 0, helped: 0, hurt: 0 }, ai_effect: {} }) };
    if (path === "/api/team-profiles/evaluation") return { ok: true, json: async () => ({ model_version: "elo-poisson-v1-team-profile", sample_count: 0, baseline_brier: null, profile_brier: null, helped: 0, hurt: 0, neutral: 0, most_helpful_traits: [], most_misleading_traits: [], matches: [] }) };
    if (path === "/api/match-count-breakdown") return { ok: true, json: async () => ({ total_finished: 0, has_pre_match_prediction: 0, has_pre_kickoff_snapshot: 0, has_locked_snapshot: 0, has_fallback_snapshot: 0, actually_scored: 0, missing_snapshot: 0, details: [], scoring_snapshot_rule: "latest_pre_match_snapshot_before_kickoff" }) };
    if (path === "/api/error-attribution-summary") return { ok: true, json: async () => ({ draw_underestimated: 0, favorite_overestimated: 0, underdog_underestimated: 0, overconfident_wrong: 0, low_score_draw_missed: 0, market_missing: 0, ai_missing: 0, ensemble_helped: 0, ensemble_hurt: 0 }) };
    if (path.startsWith("/api/team-profiles/") && path !== "/api/team-profiles/evaluation") return { ok: true, json: async () => ({ profile: mockTeamProfile(), summary: "防守优先，大赛经验丰富" }) };
    if (path === "/api/team-profiles") return { ok: true, json: async () => ({ profiles: [], total: 0 }) };
    return { ok: true, json: async () => dashboardPayload };
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

// P4.2: Navigation has 4 main entries
it("shows exactly 4 main navigation buttons", async () => {
  renderApp();
  const navButtons = await screen.findAllByRole("tab", { name: /今日工作台|比赛中心|模型复盘|冠军与赛程/ });
  expect(navButtons).toHaveLength(4);
});

// Default page is Daily Dashboard
it("defaults to 今日工作台 on load", async () => {
  renderApp();
  expect(await screen.findByRole("tab", { name: "今日工作台" })).toHaveClass("active");
});

it("does not show the legacy header sync button on the daily dashboard", async () => {
  const { container } = renderApp();
  expect(await screen.findByText("2026 世界杯预测工作台")).toBeVisible();
  expect(container.querySelector(".app-header__sync-btn")).toBeNull();
});

// Daily dashboard shows today's status
it("shows today's status on daily dashboard", async () => {
  renderApp();
  expect(await screen.findByText(/今日状态/)).toBeVisible();
});

it("separates pre-match snapshot status from AI and Ensemble records in finished review", async () => {
  renderApp(dashboardWithFinishedAiOnlyReview());

  expect(await screen.findByText("赛前快照：无")).toBeVisible();
  expect(screen.getByText("AI：有")).toBeVisible();
  expect(screen.getByText("Ensemble：有")).toBeVisible();
  expect(screen.getByText("未纳入：无赛前快照")).toBeVisible();
  expect(screen.queryByText("赛前预测：无")).not.toBeInTheDocument();
});

// Daily dashboard shows workflow action buttons
it("shows action buttons on daily dashboard", async () => {
  renderApp();
  const actionHeader = await screen.findByText("操作");
  const sectionCard = actionHeader.closest(".section-card")!;
  const expandBtn = within(sectionCard as HTMLElement).queryByText("展开");
  if (expandBtn) await userEvent.click(expandBtn);
  expect(await screen.findByText(/更新今日数据/)).toBeVisible();
});

it("does not auto-trigger daily update when the page loads", async () => {
  renderApp(dashboard, workflowStatus({
    today_status: "needs_run",
    recommended_action: "run_daily_open_workflow",
    next_action: { message: "建议运行每日更新", action: "run_daily_open_workflow" },
  }));

  expect(await screen.findByRole("button", { name: "立即更新" })).toBeVisible();
  await new Promise((resolve) => setTimeout(resolve, 0));

  const calls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls;
  expect(calls.some(([url, init]) => String(url).includes("/api/workflows/daily-open") && init?.method === "POST")).toBe(false);
});

it("shows workflow progress percent on the running daily action button", async () => {
  renderApp(dashboard, workflowStatus({
    today_status: "running",
    last_run: {
      id: 7,
      workflow_type: "post_match",
      trigger_source: "manual",
      status: "running",
      started_at: "2026-06-13T08:00:00Z",
      finished_at: null,
      duration_seconds: null,
      steps: [],
      summary: null,
      error_message: null,
      progress: { total_steps: 9, completed_steps: 4, percent: 44, running_step: "update_results", failed_steps: [] },
    },
  }));
  const actionHeader = await screen.findByText("操作");
  const sectionCard = actionHeader.closest(".section-card")!;
  const expandBtn = within(sectionCard as HTMLElement).queryByText("展开");
  if (expandBtn) await userEvent.click(expandBtn);

  expect(await screen.findByRole("button", { name: "同步赛果 44%" })).toBeDisabled();
  expect(screen.getByRole("progressbar", { name: "同步赛果进度" })).toHaveAttribute("aria-valuenow", "44");
});

it("sends with_ai when running AI predictions from daily dashboard", async () => {
  renderApp();
  const actionHeader = await screen.findByText("操作");
  const sectionCard = actionHeader.closest(".section-card")!;
  const expandBtn = within(sectionCard as HTMLElement).queryByText("展开");
  if (expandBtn) await userEvent.click(expandBtn);

  await userEvent.click(await screen.findByRole("button", { name: "运行 AI 预测" }));

  expect(globalThis.fetch).toHaveBeenCalledWith(
    "/api/workflows/pre-match",
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ with_ai: true }),
    }),
  );
});

// Can navigate to match center
it("navigates to match center", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("tab", { name: "比赛中心" }));
  expect(screen.getByRole("tab", { name: "比赛中心" })).toHaveClass("active");
});

// Can navigate to model review
it("navigates to model review center", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  expect(screen.getByRole("tab", { name: "模型复盘" })).toHaveClass("active");
});

it("keeps the main navigation outside the header after switching pages", async () => {
  const { container } = renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));

  expect(container.querySelector(".app-header .nav-tabs")).toBeNull();
  expect(container.querySelectorAll(".nav-tabs[role='tablist']")).toHaveLength(1);
});

// Can navigate to tournament center
it("navigates to tournament center", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "冠军与赛程" });
  await userEvent.click(screen.getByRole("tab", { name: "冠军与赛程" }));
  expect(screen.getByRole("tab", { name: "冠军与赛程" })).toHaveClass("active");
});

// Match center has tabs
it("shows tabs in match center", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("tab", { name: "比赛中心" }));
  // Tab labels appear as buttons inside the match center
  const future24Tabs = await screen.findAllByText(/未来 ?24 小时比赛/);
  expect(future24Tabs.length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("全部比赛").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("分组赛").length).toBeGreaterThanOrEqual(1);
  expect(screen.getAllByText("淘汰赛").length).toBeGreaterThanOrEqual(1);
});

// Group dashboard still works inside match center
it("shows group dashboard inside match center groups tab", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("tab", { name: "比赛中心" }));
  await screen.findByText("分组赛");
  await userEvent.click(screen.getByText("分组赛"));
  expect(await screen.findByRole("heading", { name: "Group A" })).toBeVisible();
});

// Model review shows sample sufficiency
it("shows sample sufficiency in model review", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  // Wait for the model review content to load
  const sampleElements = await screen.findAllByText(/样本/);
  expect(sampleElements.length).toBeGreaterThanOrEqual(1);
});

// Tournament center shows champion probability tab
it("shows champion probability tab in tournament center", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "冠军与赛程" });
  await userEvent.click(screen.getByRole("tab", { name: "冠军与赛程" }));
  expect(await screen.findByText("冠军概率")).toBeVisible();
  expect(screen.getByText("晋级概率")).toBeVisible();
  expect(screen.getByText("淘汰赛路径")).toBeVisible();
});

// Team Profile: model review shows profile evaluation section
it("shows profile evaluation section in model review", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  expect(await screen.findByText(/球队画像模型表现/)).toBeVisible();
});

it("renders model review even if the legacy model-score endpoint fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const path = new URL(String(input), "http://localhost").pathname;
    if (path === "/api/model-score") return { ok: false, status: 500, json: async () => ({}) };
    if (path === "/api/model-score/details") return { ok: true, json: async () => ({ details: [], exclusions: [] }) };
    if (path === "/api/model-score/by-version") return { ok: true, json: async () => ({ versions: [] }) };
    if (path === "/api/accuracy-command-center") return { ok: true, json: async () => ({ sample_count: 0, version_scores: [], model_comparison: [], baseline_score: { available: false, sample_count: 0 }, model_recommendation: { recommended_model_version: "elo-poisson-v1" }, scoring_exclusions: [] }) };
    if (path === "/api/ai-evaluation") return { ok: true, json: async () => ({ system: { sample_count: 0 }, ai_by_version: {}, ensemble: { sample_count: 0, helped: 0, hurt: 0 }, ai_effect: {} }) };
    if (path === "/api/team-profiles/evaluation") return { ok: true, json: async () => ({ model_version: "elo-poisson-v1-team-profile", sample_count: 0, baseline_brier: null, profile_brier: null, helped: 0, hurt: 0, neutral: 0, most_helpful_traits: [], most_misleading_traits: [], matches: [] }) };
    if (path === "/api/match-count-breakdown") return { ok: true, json: async () => ({ total_finished: 0, has_pre_match_prediction: 0, has_pre_kickoff_snapshot: 0, has_locked_snapshot: 0, has_fallback_snapshot: 0, actually_scored: 0, missing_snapshot: 0, details: [], scoring_snapshot_rule: "latest_pre_match_snapshot_before_kickoff" }) };
    if (path === "/api/error-attribution-summary") return { ok: true, json: async () => ({ draw_underestimated: 0, favorite_overestimated: 0, underdog_underestimated: 0, overconfident_wrong: 0, low_score_draw_missed: 0, market_missing: 0, ai_missing: 0, ensemble_helped: 0, ensemble_hurt: 0 }) };
    if (path === "/api/adaptive-weights") return { ok: true, json: async () => ({ weights: { system: 1 }, performance: {}, is_adaptive: false, significance: {}, last_updated: null, config: { algorithm: "bayesian_model_averaging_v2", min_sample_size: 10, max_weight_shift: 0.12, hedge_eta: 1.5, time_decay_half_life: 20, significance_level: 0.10, floor_weight: 0.05, max_lookback: 60 } }) };
    if (path === "/api/workflows/status") return { ok: true, json: async () => workflowStatus() };
    if (path === "/api/workflows/runs") return { ok: true, json: async () => ({ runs: [] }) };
    if (path === "/api/dashboard") return { ok: true, json: async () => dashboard };
    if (path.startsWith("/api/team-profiles/")) return { ok: true, json: async () => ({ profile: mockTeamProfile(), summary: "防守优先，大赛经验丰富" }) };
    if (path === "/api/tournament/projections") return { ok: true, json: async () => ({ teams: [], source: "simulation" }) };
    if (path === "/api/tournament/bracket") return { ok: true, json: async () => ({ rounds: [] }) };
    return { ok: true, json: async () => ({}) };
  }));

  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  expect(await screen.findByText("核心结论")).toBeVisible();
});

// Team Profile: model review shows profile Brier metrics
it("shows profile Brier metrics in model review", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  // Profile evaluation section should show sample count and Brier labels
  const profileSection = await screen.findByText(/球队画像模型表现/);
  expect(profileSection).toBeVisible();
  // Check that profile-related metrics are displayed
  expect(screen.getByText(/Profile Brier/)).toBeVisible();
});

// Team Profile: model review shows sourced profile evaluation label
it("shows sourced profile data label in profile evaluation", async () => {
  renderApp();
  await screen.findByRole("tab", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("tab", { name: "模型复盘" }));
  // The profile evaluation should reference the team profile model version
  expect(await screen.findByText(/elo-poisson-v1-team-profile/)).toBeVisible();
});
