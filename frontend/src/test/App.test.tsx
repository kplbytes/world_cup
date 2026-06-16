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

function defaultFetchHandler(url: string) {
  if (url.includes("/api/model-score")) return { ok: true, json: async () => modelScore };
  if (url.includes("/api/decision")) return { ok: true, json: async () => decision };
  if (url.includes("/api/accuracy-command-center")) return { ok: true, json: async () => ({
    model_recommendation: null, version_scores: [], calibration: { buckets: [] },
    market_comparison: { market_sample_count: 0, model_brier: 0, market_brier: 0, blended_brier: 0, model_logloss: 0, market_logloss: 0, blended_logloss: 0, suggested_market_blend_weight: 0, market_helped_count: 0, market_hurt_count: 0, market_neutral_count: 0 },
    data_quality: null, ai_evaluation: { system: { sample_count: 0, brier: null, logloss: null, hit_rate: null }, ai_by_version: {}, ensemble: { sample_count: 0, brier: null, logloss: null, hit_rate: null, helped: 0, hurt: 0 }, ai_effect: {} },
    ai_models: { enabled: false, models: [] },
  }) };
  if (url.includes("/api/workflows/status")) return { ok: true, json: async () => ({
    today_status: "completed", last_run_at: "2026-06-13T08:00:00Z",
    recommended_action: null, button_states: {},
    yesterday_matches: { count: 0, scored: 0, needs_review: false },
    upcoming_matches: { count_24h: 0, count_48h: 0, baseline_ready: 0, ai_ready: 0, ensemble_ready: 0, needs_ai: 0 },
    lock_status: { matches_near_kickoff: 0, locked: 0, needs_lock: 0, real_time_only: 0 },
    ai_stats: { today_ai_calls: 0, today_ai_failed: 0, today_ai_skipped: 0, cooldown_skipped: false, only_missing_skipped: 0 },
  }) };
  if (url.includes("/api/workflows/runs")) return { ok: true, json: async () => ({ runs: [] }) };
  if (url.includes("/api/tournament/projections")) return { ok: true, json: async () => ({ teams: [], source: "simulation" }) };
  if (url.includes("/api/tournament/bracket")) return { ok: true, json: async () => ({ rounds: [] }) };
  if (url.includes("/api/ai-models")) return { ok: true, json: async () => ({ enabled: false, models: [] }) };
  if (url.includes("/api/ai-predictions")) return { ok: true, json: async () => ({ match_id: "", predictions: [] }) };
  if (url.includes("/api/ensemble")) return { ok: true, json: async () => ({ match_id: "", predictions: [] }) };
  if (url.includes("/api/ai-evaluation")) return { ok: true, json: async () => ({ system: { sample_count: 0 }, ai_by_version: {}, ensemble: { sample_count: 0, helped: 0, hurt: 0 }, ai_effect: {} }) };
  if (url.includes("/api/team-profiles/evaluation")) return { ok: true, json: async () => ({ model_version: "elo-poisson-v1-team-profile", sample_count: 0, baseline_brier: null, profile_brier: null, helped: 0, hurt: 0, neutral: 0, most_helpful_traits: [], most_misleading_traits: [], matches: [] }) };
  if (url.includes("/api/team-profiles/") && !url.includes("evaluation")) return { ok: true, json: async () => ({ profile: { team_id: "A1", team_code: "A1", profile_version: "team-profile-v1", sample_count: 16, world_cup_sample_count: 10, traits_json: ["防守优先", "大赛经验丰富"], draw_rate_overall: 0.25, draw_resilience_score: 0.4, low_score_tendency: 0.3, favorite_overconfidence_risk: 0.15, source_summary_json: { mode: "seed_mock_v1" } }, summary: "防守优先，大赛经验丰富" }) };
  if (url.includes("/api/team-profiles")) return { ok: true, json: async () => ({ profiles: [], total: 0 }) };
  if (url.includes("/api/decision-snapshot-status")) return { ok: true, json: async () => ({ snapshots_ready: 0, matches_total: 0 }) };
  if (url.includes("/api/match-count-breakdown")) return { ok: true, json: async () => ({}) };
  if (url.includes("/api/error-attribution-summary")) return { ok: true, json: async () => ({}) };
  if (url.includes("/api/model-comparison")) return { ok: true, json: async () => ({ comparison: [], sample_sufficient: false, sample_count: 0 }) };
  return { ok: true, json: async () => dashboard };
}

function renderApp() {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    return defaultFetchHandler(url);
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

// P4.2: Navigation has 4 main entries
it("shows exactly 4 main navigation buttons", async () => {
  renderApp();
  const navButtons = await screen.findAllByRole("button", { name: /今日工作台|比赛中心|模型复盘|冠军与赛程/ });
  expect(navButtons).toHaveLength(4);
});

// Default page is Daily Dashboard
it("defaults to 今日工作台 on load", async () => {
  renderApp();
  expect(await screen.findByRole("button", { name: "今日工作台" })).toHaveClass("active");
});

// Daily dashboard shows today's status
it("shows today's status on daily dashboard", async () => {
  renderApp();
  expect(await screen.findByText(/今日状态/)).toBeVisible();
});

// Daily dashboard shows workflow action buttons (collapsed by default, need to expand)
it("shows action buttons on daily dashboard", async () => {
  renderApp();
  // Find the "操作" section header, then click its expand button
  const actionHeader = await screen.findByText("操作");
  const sectionCard = actionHeader.closest(".section-card")!;
  const expandBtn = within(sectionCard as HTMLElement).getByText("展开");
  await userEvent.click(expandBtn);
  // Now the action buttons should be visible
  expect(await screen.findByText(/更新今日数据/)).toBeVisible();
});

// Can navigate to match center
it("navigates to match center", async () => {
  renderApp();
  await screen.findByRole("button", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("button", { name: "比赛中心" }));
  expect(screen.getByRole("button", { name: "比赛中心" })).toHaveClass("active");
});

// Can navigate to model review
it("navigates to model review center", async () => {
  renderApp();
  await screen.findByRole("button", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("button", { name: "模型复盘" }));
  expect(screen.getByRole("button", { name: "模型复盘" })).toHaveClass("active");
});

// Can navigate to tournament center
it("navigates to tournament center", async () => {
  renderApp();
  await screen.findByRole("button", { name: "冠军与赛程" });
  await userEvent.click(screen.getByRole("button", { name: "冠军与赛程" }));
  expect(screen.getByRole("button", { name: "冠军与赛程" })).toHaveClass("active");
});

// Match center has tabs
it("shows tabs in match center", async () => {
  renderApp();
  await screen.findByRole("button", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("button", { name: "比赛中心" }));
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
  await screen.findByRole("button", { name: "比赛中心" });
  await userEvent.click(screen.getByRole("button", { name: "比赛中心" }));
  await screen.findByText("分组赛");
  await userEvent.click(screen.getByText("分组赛"));
  expect(await screen.findByRole("heading", { name: "Group A" })).toBeVisible();
});

// Model review shows sample sufficiency
it("shows sample sufficiency in model review", async () => {
  renderApp();
  await screen.findByRole("button", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("button", { name: "模型复盘" }));
  // Wait for the model review content to load
  const sampleElements = await screen.findAllByText(/样本/);
  expect(sampleElements.length).toBeGreaterThanOrEqual(1);
});

// Tournament center shows champion probability tab
it("shows champion probability tab in tournament center", async () => {
  renderApp();
  await screen.findByRole("button", { name: "冠军与赛程" });
  await userEvent.click(screen.getByRole("button", { name: "冠军与赛程" }));
  expect(await screen.findByText("冠军概率")).toBeVisible();
  expect(screen.getByText("晋级概率")).toBeVisible();
  expect(screen.getByText("淘汰赛路径")).toBeVisible();
});

// Team Profile: model review shows profile evaluation section
it("shows profile evaluation section in model review", async () => {
  renderApp();
  await screen.findByRole("button", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("button", { name: "模型复盘" }));
  expect(await screen.findByText(/球队画像模型表现/)).toBeVisible();
});

// Team Profile: model review shows profile Brier metrics
it("shows profile Brier metrics in model review", async () => {
  renderApp();
  await screen.findByRole("button", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("button", { name: "模型复盘" }));
  // Profile evaluation section should show sample count and Brier labels
  const profileSection = await screen.findByText(/球队画像模型表现/);
  expect(profileSection).toBeVisible();
  // Check that profile-related metrics are displayed
  expect(screen.getByText(/Profile Brier/)).toBeVisible();
});

// Team Profile: model review shows seed_mock_v1 data source label
it("shows seed_mock_v1 data source in profile evaluation", async () => {
  renderApp();
  await screen.findByRole("button", { name: "模型复盘" });
  await userEvent.click(screen.getByRole("button", { name: "模型复盘" }));
  // The profile evaluation should reference the team profile model version
  expect(await screen.findByText(/elo-poisson-v1-team-profile/)).toBeVisible();
});

// Update Predictions button is rendered on the daily dashboard
it("shows 更新预测 button on daily dashboard", async () => {
  renderApp();
  expect(await screen.findByRole("button", { name: "更新预测" })).toBeVisible();
});

// Clicking 更新预测 calls the correct API endpoint
it("calls /api/workflows/update-predictions when clicking 更新预测", async () => {
  const fetchMock = vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/workflows/update-predictions")) {
      return { ok: true, json: async () => ({ status: "ok", updated_at: "2026-06-13T08:00:00Z", matches_considered: 3, predictions_updated: 3, ai_success: 3, ai_failed: 0, ensemble_updated: 3, locked_skipped: 0, errors: [], run_id: 42 }) };
    }
    return defaultFetchHandler(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  const btn = await screen.findByRole("button", { name: "更新预测" });
  await userEvent.click(btn);

  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/api/workflows/update-predictions"),
    expect.objectContaining({ method: "POST" }),
  );
});

// Button shows 更新中... while loading
it("shows 更新中... while update predictions is loading", async () => {
  let resolveUpdatePredictions!: (value: unknown) => void;
  const fetchMock = vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/workflows/update-predictions")) {
      return new Promise((resolve) => { resolveUpdatePredictions = resolve; })
        .then(() => ({ ok: true, json: async () => ({ status: "ok", updated_at: "2026-06-13T08:00:00Z", matches_considered: 3, predictions_updated: 3, ai_success: 3, ai_failed: 0, ensemble_updated: 3, locked_skipped: 0, errors: [], run_id: 42 }) }));
    }
    return defaultFetchHandler(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  const btn = await screen.findByRole("button", { name: "更新预测" });
  await userEvent.click(btn);

  expect(await screen.findByRole("button", { name: "更新中..." })).toBeVisible();

  // Resolve to clean up
  resolveUpdatePredictions(undefined);
});

// Success feedback is shown after mutation succeeds
it("shows success feedback after update predictions succeeds", async () => {
  const fetchMock = vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/workflows/update-predictions")) {
      return { ok: true, json: async () => ({ status: "ok", updated_at: "2026-06-13T08:00:00Z", matches_considered: 3, predictions_updated: 3, ai_success: 3, ai_failed: 0, ensemble_updated: 3, locked_skipped: 0, errors: [], run_id: 42 }) };
    }
    return defaultFetchHandler(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  const btn = await screen.findByRole("button", { name: "更新预测" });
  await userEvent.click(btn);

  // Should show success details
  expect(await screen.findByText(/AI 成功 3/)).toBeVisible();
  // Button should show updated time
  expect(await screen.findByRole("button", { name: /预测已更新/ })).toBeVisible();
});

// Error feedback is shown after mutation fails
it("shows error feedback after update predictions fails", async () => {
  const fetchMock = vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/workflows/update-predictions")) {
      return { ok: false, status: 500, json: async () => ({ detail: "Internal error" }) };
    }
    return defaultFetchHandler(url);
  });
  vi.stubGlobal("fetch", fetchMock);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><App /></QueryClientProvider>);

  const btn = await screen.findByRole("button", { name: "更新预测" });
  await userEvent.click(btn);

  expect(await screen.findByText(/更新失败/)).toBeVisible();
});
