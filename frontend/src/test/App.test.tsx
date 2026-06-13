import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
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

function renderApp() {
  vi.stubGlobal("fetch", vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    return {
      ok: true,
      json: async () => url.includes("/api/model-score")
        ? modelScore
        : url.includes("/api/decision")
          ? decision
          : dashboard,
    };
  }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}><App /></QueryClientProvider>);
}

it("switches from Group A to Group L and shows its six matches", async () => {
  renderApp();
  await screen.findByRole("heading", { name: "Group A" });
  await userEvent.click(screen.getByRole("button", { name: "L" }));
  expect(await screen.findByRole("heading", { name: "Group L" })).toBeVisible();
  expect(screen.getAllByTestId("match-card")).toHaveLength(6);
});

it("opens all matches and team detail", async () => {
  renderApp();
  await screen.findByRole("heading", { name: "Group A" });
  await userEvent.click(screen.getAllByRole("button", { expanded: false })[0]);
  expect(await screen.findByText(/人工修正/)).toBeVisible();
  expect(screen.getByText(/主力前锋伤缺/)).toBeVisible();
  expect(screen.getByText(/建议人工核查/)).toBeVisible();
  await userEvent.click(screen.getByRole("button", { name: "全部比赛" }));
  expect(await screen.findByRole("heading", { name: "全部比赛" })).toBeVisible();
  expect(screen.getAllByTestId("match-card")).toHaveLength(72);
  await userEvent.click(screen.getByRole("button", { name: "分组看板" }));
  await userEvent.click(screen.getByRole("button", { name: "查看 Team A1" }));
  expect(await screen.findByLabelText("Team A1 球队详情")).toBeVisible();
  expect(screen.getByText(/球员名单与实时身价/)).toBeVisible();
});

it("shows the pre-match prediction in post-match review", async () => {
  renderApp();
  await screen.findByRole("heading", { name: "Group A" });
  await userEvent.click(screen.getByRole("button", { name: "决策视图" }));

  expect(await screen.findByText(/预测：主胜/)).toBeVisible();
  expect(screen.getByText("命中")).toBeVisible();
  expect(screen.getAllByText(/Brier/).length).toBeGreaterThan(0);
  expect(screen.getByText(/低估了净胜优势/)).toBeVisible();
  expect(screen.getByText(/模型版本迭代/)).toBeVisible();
  expect(screen.getByText(/对比基线：elo-poisson-v1/)).toBeVisible();
});
