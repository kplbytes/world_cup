import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi, beforeAll } from "vitest";

import DataHealthBadge from "../components/DataHealthBadge";
import * as api from "../api";
import type { DataHealthReport } from "../types";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const REAL_DATA: DataHealthReport = {
  total_historical_matches: 7877,
  time_coverage: { earliest: "2018-01-02", latest: "2026-06-13" },
  national_team_coverage: { total_teams: 48, teams_with_data: 48, coverage_rate: 1.0 },
  last_update: "2026-06-15T10:00:00Z",
  unmapped_team_count: 238,
  mock_record_count: 0,
  real_profile_count: 48,
  mock_profile_count: 0,
  uses_real_data: true,
  date_only_count: 8115,
  exact_count: 0,
  excluded_extra_time_count: 164,
  checked_at: "2026-06-15T10:00:00Z",
};

const MOCK_DATA: DataHealthReport = {
  total_historical_matches: 0,
  time_coverage: { earliest: null, latest: null },
  national_team_coverage: { total_teams: 48, teams_with_data: 0, coverage_rate: 0 },
  last_update: null,
  unmapped_team_count: 0,
  mock_record_count: 768,
  real_profile_count: 0,
  mock_profile_count: 48,
  uses_real_data: false,
  date_only_count: 0,
  exact_count: 0,
  excluded_extra_time_count: 0,
  checked_at: "2026-06-15T10:00:00Z",
};

const MIXED_DATA: DataHealthReport = {
  ...REAL_DATA,
  mock_record_count: 100,
  mock_profile_count: 2,
};

function renderWithQueryClient(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>{ui}</QueryClientProvider>,
  );
}

it("shows loading state when data is not yet available", () => {
  vi.spyOn(api, "getDataHealth").mockImplementation(() => new Promise(() => {}));
  renderWithQueryClient(<DataHealthBadge />);
  expect(screen.getByText(/加载中/)).toBeTruthy();
});

it("shows real data status (green) when uses_real_data=true and mock=0", async () => {
  vi.spyOn(api, "getDataHealth").mockResolvedValue(REAL_DATA);
  renderWithQueryClient(<DataHealthBadge />);

  const btn = await screen.findByText("真实数据");
  expect(btn).toBeTruthy();
});

it("shows mock data warning (red) when uses_real_data=false", async () => {
  vi.spyOn(api, "getDataHealth").mockResolvedValue(MOCK_DATA);
  renderWithQueryClient(<DataHealthBadge />);

  const btn = await screen.findByText("模拟数据");
  expect(btn).toBeTruthy();
});

it("shows mixed data warning (yellow) when uses_real_data=true but mock>0", async () => {
  vi.spyOn(api, "getDataHealth").mockResolvedValue(MIXED_DATA);
  renderWithQueryClient(<DataHealthBadge />);

  const btn = await screen.findByText("混合数据");
  expect(btn).toBeTruthy();
});

it("shows detail fields when clicked", async () => {
  vi.spyOn(api, "getDataHealth").mockResolvedValue(REAL_DATA);
  renderWithQueryClient(<DataHealthBadge />);

  const btn = await screen.findByText("真实数据");
  await userEvent.click(btn);

  // Detail panel should show key fields
  expect(screen.getByText(/数据健康详情/)).toBeTruthy();
  expect(screen.getByText(/7877/)).toBeTruthy(); // total matches
  expect(screen.getByText(/164/)).toBeTruthy(); // excluded extra time
});

it("shows API error state gracefully", async () => {
  vi.spyOn(api, "getDataHealth").mockRejectedValue(new Error("Network error"));
  renderWithQueryClient(<DataHealthBadge />);

  // Should show loading initially, then stay in loading/error state
  const el = screen.getByText(/加载中|数据健康/);
  expect(el).toBeTruthy();
});
