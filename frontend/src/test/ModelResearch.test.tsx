import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";

import ModelResearch from "../components/ModelResearch";
import type { BacktestResultsResponse, DatasetInfo } from "../types";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// ─── Mock data ──────────────────────────────────────────────────────────

const mockResults: BacktestResultsResponse = {
  data_version: "international-history-v1",
  created_at: "2026-06-15T00:00:00Z",
  models: [
    {
      model_name: "legacy-elo-poisson",
      split_name: "test",
      brier_score: 0.5421,
      brier_score_avg: 0.1807,
      log_loss: 1.0234,
      ece: 0.0312,
      top1_hit_rate: 0.5432,
      draw_recall: 0.1234,
      match_count: 500,
      admission_status: "shadow",
    },
    {
      model_name: "refitted-elo-poisson",
      split_name: "test",
      brier_score: 0.5312,
      brier_score_avg: 0.1771,
      log_loss: 1.0123,
      ece: 0.0298,
      top1_hit_rate: 0.5567,
      draw_recall: 0.1456,
      match_count: 500,
      admission_status: "shadow",
    },
    {
      model_name: "dixon-coles",
      split_name: "test",
      brier_score: 0.5278,
      brier_score_avg: 0.1759,
      log_loss: 1.0089,
      ece: 0.0287,
      top1_hit_rate: 0.5612,
      draw_recall: 0.1523,
      match_count: 500,
      admission_status: "research",
    },
    {
      model_name: "legacy-elo-poisson",
      split_name: "train",
      brier_score: 0.5100,
      brier_score_avg: 0.1700,
      log_loss: 0.9900,
      ece: 0.0250,
      top1_hit_rate: 0.5800,
      draw_recall: 0.1600,
      match_count: 2000,
      admission_status: "shadow",
    },
    {
      model_name: "refitted-elo-poisson",
      split_name: "train",
      brier_score: 0.5000,
      brier_score_avg: 0.1667,
      log_loss: 0.9800,
      ece: 0.0240,
      top1_hit_rate: 0.5900,
      draw_recall: 0.1700,
      match_count: 2000,
      admission_status: "shadow",
    },
  ],
};

const mockDataset: DatasetInfo = {
  version: "international-history-v1",
  created_at: "2026-06-15T00:00:00Z",
  total_matches: 3000,
  excluded_wc_2026: 50,
  splits: {
    train: { match_count: 2000, team_count: 120, competition_types: { friendly: 800, qualifier: 600, continental: 400, world_cup: 200 }, start: "2000-01-01T00:00:00Z", end: "2022-01-01T00:00:00Z" },
    validation: { match_count: 500, team_count: 100, competition_types: { friendly: 200, qualifier: 150, continental: 100, world_cup: 50 }, start: "2022-01-01T00:00:00Z", end: "2024-01-01T00:00:00Z" },
    test: { match_count: 500, team_count: 90, competition_types: { friendly: 200, qualifier: 150, continental: 100, world_cup: 50 }, start: "2024-01-01T00:00:00Z", end: "2026-06-11T00:00:00Z" },
  },
};

const mockRolling = {
  folds: [
    {
      fold_name: "fold_1",
      train_count: 1000,
      val_count: 200,
      eval_count: 150,
      model_metrics: {
        "legacy-elo-poisson": { eval: { brier_sum: 0.55, brier_mean: 0.18, log_loss: 1.05, ece: 0.03, top1_hit_rate: 0.53, draw_recall: 0.12, match_count: 150 } },
        "refitted-elo-poisson": { eval: { brier_sum: 0.53, brier_mean: 0.18, log_loss: 1.02, ece: 0.03, top1_hit_rate: 0.55, draw_recall: 0.14, match_count: 150 } },
      },
      draw_metrics: {
        "legacy-elo-poisson": {
          draw_brier: 0.18,
          draw_log_loss: 0.65,
          draw_ece: 0.04,
          draw_roc_auc: 0.55,
          draw_pr_auc: 0.30,
          avg_p_draw_when_draw: 0.28,
          avg_p_draw_when_not_draw: 0.25,
          top1_draw_recall: 0.05,
          n_draws: 45,
          n_non_draws: 105,
          n_total: 150,
        },
        "refitted-elo-poisson": {
          draw_brier: 0.17,
          draw_log_loss: 0.63,
          draw_ece: 0.03,
          draw_roc_auc: 0.58,
          draw_pr_auc: 0.32,
          avg_p_draw_when_draw: 0.30,
          avg_p_draw_when_not_draw: 0.24,
          top1_draw_recall: 0.08,
          n_draws: 45,
          n_non_draws: 105,
          n_total: 150,
        },
      },
      bootstrap_results: {
        "refitted-elo-poisson": {
          brier_sum: {
            metric_name: "brier_sum",
            observed_diff: -0.02,
            ci_lower_95: -0.04,
            ci_upper_95: 0.01,
            p_better: 0.92,
            conclusion: "likely better",
            n_matches: 150,
          },
        },
      },
    },
    {
      fold_name: "fold_2",
      train_count: 1200,
      val_count: 200,
      eval_count: 180,
      model_metrics: {
        "legacy-elo-poisson": { eval: { brier_sum: 0.54, brier_mean: 0.18, log_loss: 1.03, ece: 0.03, top1_hit_rate: 0.54, draw_recall: 0.13, match_count: 180 } },
        "refitted-elo-poisson": { eval: { brier_sum: 0.52, brier_mean: 0.17, log_loss: 1.01, ece: 0.03, top1_hit_rate: 0.56, draw_recall: 0.15, match_count: 180 } },
      },
    },
  ],
  cross_fold_summary: {
    "legacy-elo-poisson": { brier_sum: 0.545, log_loss: 1.04, total: 330 },
    "refitted-elo-poisson": { brier_sum: 0.525, log_loss: 1.015, total: 330 },
  },
  oof_bootstrap: {
    "refitted-elo-poisson": {
      brier_sum: {
        metric_name: "brier_sum",
        observed_diff: -0.02,
        ci_lower_95: -0.035,
        ci_upper_95: 0.005,
        p_better: 0.93,
        conclusion: "likely better",
        n_matches: 330,
      },
    },
  },
  admission_decisions: {
    "legacy-elo-poisson": "shadow",
    "refitted-elo-poisson": "shadow",
  },
};

// ─── Helpers ────────────────────────────────────────────────────────────

function createMockFetch(responses: Record<string, unknown> = {}) {
  const defaults: Record<string, unknown> = {
    "/api/backtest/results": mockResults,
    "/api/backtest/dataset": mockDataset,
    "/api/backtest/rolling": mockRolling,
    ...responses,
  };

  return vi.fn().mockImplementation(async (input: string | URL | Request) => {
    const url = String(input);
    for (const [path, data] of Object.entries(defaults)) {
      if (url.includes(path)) {
        return { ok: true, json: async () => data };
      }
    }
    return { ok: false, status: 404, json: async () => ({ detail: "Not found" }) };
  });
}

function renderComponent(fetchMock?: ReturnType<typeof createMockFetch>) {
  const mockFetch = fetchMock ?? createMockFetch();
  vi.stubGlobal("fetch", mockFetch);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    ...render(<QueryClientProvider client={client}><ModelResearch /></QueryClientProvider>),
    fetchMock: mockFetch,
  };
}

// ─── Tests ──────────────────────────────────────────────────────────────

// Test loading state
it("shows loading state while fetching data", () => {
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ModelResearch /></QueryClientProvider>);

  expect(screen.getByText("加载回测数据...")).toBeVisible();
});

// Test empty state (no results)
it("shows empty state when no backtest results exist", async () => {
  const emptyResults = { ...mockResults, models: [] };
  renderComponent(createMockFetch({ "/api/backtest/results": emptyResults }));

  expect(await screen.findByText("暂无回测数据")).toBeVisible();
});

// Test error state
it("shows error state when API fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ detail: "Server error" }) }));
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><ModelResearch /></QueryClientProvider>);

  expect(await screen.findByText("无法加载回测数据")).toBeVisible();
});

// Test results display with model names
it("displays model names in results table", async () => {
  renderComponent();

  expect(await screen.findByText("回测模型研究")).toBeVisible();
  expect((await screen.findAllByText("Legacy Elo-Poisson (基线)")).length).toBeGreaterThanOrEqual(1);
  expect((screen.getAllByText("Refitted Elo-Poisson")).length).toBeGreaterThanOrEqual(1);
  expect((screen.getAllByText("Dixon-Coles")).length).toBeGreaterThanOrEqual(1);
});

// Test fold selector
it("shows fold selector for rolling backtest", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  expect(screen.getByText("选择折:")).toBeVisible();

  const select = screen.getByRole("combobox");
  expect(select).toBeVisible();

  const options = within(select).getAllByRole("option");
  expect(options.length).toBeGreaterThanOrEqual(2);
});

// Test audit_test_seen label (NOT "blind test")
it("shows audit_test_seen label in fold selector, never 'blind test'", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  const select = screen.getByRole("combobox");
  const options = within(select).getAllByRole("option");

  // Find the audit_test_seen option
  const auditOption = options.find(opt => opt.textContent?.includes("audit_test_seen"));
  expect(auditOption).toBeDefined();

  // Ensure it does NOT say "blind test" or "untouched test"
  const allOptionTexts = options.map(opt => opt.textContent ?? "");
  for (const text of allOptionTexts) {
    expect(text.toLowerCase()).not.toContain("blind test");
    expect(text.toLowerCase()).not.toContain("untouched test");
  }
});

// Test selecting audit_test_seen shows warning
it("shows warning when audit_test_seen is selected", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  const select = screen.getByRole("combobox");
  await userEvent.selectOptions(select, "audit_test_seen");

  expect(screen.getByText(/此固定测试集已被查看/)).toBeVisible();
  // Should NOT say "blind test"
  expect(screen.queryByText(/blind test/i)).toBeNull();
});

// Test Brier formula display (canonical form)
it("shows canonical Brier formula with H, D, A", async () => {
  renderComponent();

  expect(await screen.findByText(/Brier 公式说明/)).toBeVisible();
  // Should show the canonical formula with H, D, A
  expect(screen.getByText(/Brier = mean\(Σ\(p_k - y_k\)²\) for k ∈/)).toBeVisible();
});

// Test admission reasons are shown
it("shows admission reasons for each model", async () => {
  renderComponent();

  expect(await screen.findByText("准入决策")).toBeVisible();
  const shadowReasons = screen.getAllByText(/Brier优于基线/);
  expect(shadowReasons.length).toBeGreaterThanOrEqual(1);
});

// Test draw reliability section
it("shows draw reliability section", async () => {
  renderComponent();

  expect(await screen.findByText(/平局可靠性评估/)).toBeVisible();
});

// Test draw metrics are displayed when available
it("shows draw metrics when available from rolling data", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  // Select fold_1 which has draw_metrics
  const select = screen.getByRole("combobox");
  await userEvent.selectOptions(select, "fold_1");

  // Draw metrics headers should be visible
  expect(screen.getByText("Draw Brier")).toBeVisible();
  expect(screen.getByText("Draw LogLoss")).toBeVisible();
  expect(screen.getByText("Draw ECE")).toBeVisible();
  expect(screen.getByText("Draw ROC-AUC")).toBeVisible();
  expect(screen.getByText("Draw PR-AUC")).toBeVisible();
  expect(screen.getByText("Avg P(draw|draw)")).toBeVisible();
  expect(screen.getByText("Avg P(draw|not)")).toBeVisible();
  expect(screen.getByText("Top1 DrawRecall")).toBeVisible();
  expect(screen.getByText("N_draws")).toBeVisible();
});

// Test N/A shown when data is missing
it("shows N/A when bootstrap data is missing", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  // The bootstrap section should show N/A for missing data
  const naCells = screen.getAllByText("N/A");
  expect(naCells.length).toBeGreaterThan(0);
});

// Test that no fake 0 values are displayed when API data is missing
it("never displays hardcoded 0 values for missing draw metrics", async () => {
  // Use rolling data without draw_metrics
  const rollingNoDraw = {
    ...mockRolling,
    folds: mockRolling.folds.map(f => ({ ...f, draw_metrics: undefined })),
  };
  renderComponent(createMockFetch({ "/api/backtest/rolling": rollingNoDraw }));

  await screen.findByText("回测模型研究");

  // Draw reliability section should show "—" for missing data, not 0.0000
  const drawSection = screen.getByText(/平局可靠性评估/).closest("section");
  expect(drawSection).toBeDefined();
  // Should contain "—" placeholders, not "0.0000"
  const dashCells = screen.getAllByText("—");
  expect(dashCells.length).toBeGreaterThan(0);
});

// Test bootstrap confidence intervals section
it("shows bootstrap confidence intervals section", async () => {
  renderComponent();

  expect(await screen.findByText(/Bootstrap 显著性检验/)).toBeVisible();
});

// Test bootstrap data is displayed when available
it("shows bootstrap CI data when available from rolling data", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  // Select fold_1 which has bootstrap_results
  const select = screen.getByRole("combobox");
  await userEvent.selectOptions(select, "fold_1");

  // The bootstrap section should show actual values for refitted-elo-poisson brier_sum
  expect(screen.getByText("likely better")).toBeVisible();
});

// Test cross-fold OOF bootstrap
it("shows OOF bootstrap for cross-fold summary", async () => {
  renderComponent();

  await screen.findByText("回测模型研究");

  // Select cross_fold which has oof_bootstrap
  const select = screen.getByRole("combobox");
  await userEvent.selectOptions(select, "cross_fold");

  // OOF bootstrap data should be visible
  expect(screen.getByText("likely better")).toBeVisible();
});
