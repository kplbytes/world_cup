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
      source: "openfootball",
      source_updated_at: "2026-06-13T00:00:00Z",
      market: null,
      prediction: { home_xg: 1.4, away_xg: 1.0, home_win: .45, draw: .3, away_win: .25, scorelines: [{ home_goals: 1, away_goals: 0, probability: .12 }], confidence: .8, confidence_label: "高", data_confidence: .85, data_confidence_label: "高", model_confidence: .22, model_confidence_label: "中", explanation: "Model explanation", model_inputs: { home_elo: 1700, away_elo: 1600 }, model_version: "elo-poisson-v1" },
    })),
  })),
};

function renderApp() {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => dashboard }));
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
  await userEvent.click(screen.getByRole("button", { name: "全部比赛" }));
  expect(await screen.findByRole("heading", { name: "全部比赛" })).toBeVisible();
  expect(screen.getAllByTestId("match-card")).toHaveLength(72);
  await userEvent.click(screen.getByRole("button", { name: "分组看板" }));
  await userEvent.click(screen.getByRole("button", { name: "查看 Team A1" }));
  expect(await screen.findByLabelText("Team A1 球队详情")).toBeVisible();
  expect(screen.getByText(/球员名单与实时身价/)).toBeVisible();
});
