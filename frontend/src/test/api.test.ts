import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  getDashboard,
  getMatchDetail,
  refreshDashboard,
  getAIModels,
  getAIPredictions,
  getEnsemble,
  getWorkflowStatus,
  triggerDailyOpen,
  getTeamProfile,
  getDecision,
  getModelScore,
  runAIPrediction,
  runEnsemble,
  getTournamentBracket,
  getTournamentProjections,
  getErrorAttributionSummary,
  getModelComparison,
  getMatchCountBreakdown,
} from "../api";

// ── API function tests with mock fetch ──────────────────────────────

describe("API functions", () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  function mockFetchResponse(data: unknown, ok = true, status = 200) {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok,
      status,
      json: async () => data,
    });
  }

  // ── getDashboard ──
  describe("getDashboard", () => {
    it("calls /api/dashboard and returns parsed JSON", async () => {
      const mockData = {
        revision: { id: 1, created_at: "2026-06-01", model_version: "v1", simulation_iterations: 1000, simulation_seed: 42 },
        groups: [],
        data_sources: [],
      };
      mockFetchResponse(mockData);

      const result = await getDashboard();
      expect(result).toEqual(mockData);
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/dashboard",
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);

      await expect(getDashboard()).rejects.toThrow("Dashboard request failed: 500");
    });
  });

  // ── getMatchDetail ──
  describe("getMatchDetail", () => {
    it("encodes matchId in URL", async () => {
      mockFetchResponse({ id: "test-match" });

      await getMatchDetail("match/123");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/matches/match%2F123",
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 404);

      await expect(getMatchDetail("missing")).rejects.toThrow("Match detail failed: 404");
    });
  });

  // ── refreshDashboard ──
  describe("refreshDashboard", () => {
    it("sends POST to /api/refresh", async () => {
      mockFetchResponse(undefined);

      await refreshDashboard();
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/refresh",
        expect.objectContaining({
          method: "POST",
          signal: expect.any(AbortSignal),
        })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);

      await expect(refreshDashboard()).rejects.toThrow("Refresh failed: 500");
    });
  });

  // ── getAIModels ──
  describe("getAIModels", () => {
    it("calls /api/ai-models and returns data", async () => {
      const mockData = { enabled: true, models: [] };
      mockFetchResponse(mockData);

      const result = await getAIModels();
      expect(result).toEqual(mockData);
    });
  });

  // ── getAIPredictions ──
  describe("getAIPredictions", () => {
    it("encodes matchId in query params", async () => {
      mockFetchResponse({ match_id: "m1", predictions: [] });

      await getAIPredictions("m1");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ai-predictions?match_id=m1",
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      );
    });
  });

  // ── getEnsemble ──
  describe("getEnsemble", () => {
    it("calls /api/ensemble with match_id", async () => {
      mockFetchResponse({ match_id: "m1", predictions: [] });

      await getEnsemble("m1");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ensemble?match_id=m1",
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      );
    });
  });

  // ── getWorkflowStatus ──
  describe("getWorkflowStatus", () => {
    it("calls /api/workflows/status", async () => {
      mockFetchResponse({ today_status: "completed" });

      const result = await getWorkflowStatus();
      expect(result).toEqual({ today_status: "completed" });
    });
  });

  // ── triggerDailyOpen ──
  describe("triggerDailyOpen", () => {
    it("sends POST with JSON body", async () => {
      mockFetchResponse({});

      await triggerDailyOpen({ with_ai: true });
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/workflows/daily-open",
        expect.objectContaining({
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ with_ai: true }),
        })
      );
    });
  });

  // ── Timeout behavior ──
  describe("timeout handling", () => {
    it("uses default timeout of 30 seconds", async () => {
      mockFetchResponse({});

      await getDashboard();
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
      // The signal should be from an AbortController with 30s timeout
      expect(call[1]).toHaveProperty("signal");
    });

    it("passes AbortSignal to fetch for timeout support", async () => {
      mockFetchResponse({});

      await getDashboard();
      const call = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0];
      const signal = call[1]?.signal;
      expect(signal).toBeInstanceOf(AbortSignal);
      // The signal should not be aborted for a successful request
      expect(signal?.aborted).toBe(false);
    });

    it("clears timeout when request completes before timeout", async () => {
      const clearTimeoutSpy = vi.spyOn(globalThis, "clearTimeout");
      mockFetchResponse({});

      await getDashboard();
      expect(clearTimeoutSpy).toHaveBeenCalled();
      clearTimeoutSpy.mockRestore();
    });
  });

  // ── getTeamProfile ──
  describe("getTeamProfile", () => {
    it("encodes teamId in URL", async () => {
      mockFetchResponse({ profile: {}, summary: "" });

      await getTeamProfile("team/special");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/team-profiles/team%2Fspecial",
        expect.objectContaining({ signal: expect.any(AbortSignal) })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 404);
      await expect(getTeamProfile("missing")).rejects.toThrow("Team profile failed: 404");
    });
  });

  // ── getDecision ──
  describe("getDecision", () => {
    it("calls /api/decision and returns data", async () => {
      const mockData = {
        review_summary: { matches_scored: 5, brier_score: 0.25, log_loss: 0.5, outcome_hit_rate: 0.6, top_score_hit_rate: 0.2, xg_mae: 0.4 },
        today_matches: [], most_confident: [], most_uncertain: [], biggest_divergence: [], upset_risk: [], recent_review: [], intelligence_risks: [],
      };
      mockFetchResponse(mockData);

      const result = await getDecision();
      expect(result).toEqual(mockData);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getDecision()).rejects.toThrow("Decision request failed: 500");
    });
  });

  // ── getModelScore ──
  describe("getModelScore", () => {
    it("calls /api/model-score and returns data", async () => {
      mockFetchResponse({ id: 1, model_version: "v1" });

      const result = await getModelScore();
      expect(result).toEqual({ id: 1, model_version: "v1" });
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getModelScore()).rejects.toThrow("Model score request failed: 500");
    });
  });

  // ── runAIPrediction ──
  describe("runAIPrediction", () => {
    it("sends POST with match_id and default model version", async () => {
      mockFetchResponse({});

      await runAIPrediction("m1");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ai-predictions/run?match_id=m1",
        expect.objectContaining({
          method: "POST",
          signal: expect.any(AbortSignal),
        })
      );
    });

    it("sends POST with custom model version", async () => {
      mockFetchResponse({});

      await runAIPrediction("m1", "ai-deepseek-v4-flash-v1");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ai-predictions/run?match_id=m1&model_version=ai-deepseek-v4-flash-v1",
        expect.objectContaining({
          method: "POST",
          signal: expect.any(AbortSignal),
        })
      );
    });

    it("does not append model_version when it is 'default'", async () => {
      mockFetchResponse({});

      await runAIPrediction("m1", "default");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ai-predictions/run?match_id=m1",
        expect.objectContaining({ method: "POST" })
      );
    });

    it("sends force=true when manual refresh requests a re-run", async () => {
      mockFetchResponse({});

      await runAIPrediction("m1", undefined, true);
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ai-predictions/run?match_id=m1&force=true",
        expect.objectContaining({ method: "POST" })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(runAIPrediction("m1")).rejects.toThrow("AI prediction run failed: 500");
    });
  });

  // ── runEnsemble ──
  describe("runEnsemble", () => {
    it("sends POST to /api/ensemble/run", async () => {
      mockFetchResponse({});

      await runEnsemble("m1");
      expect(globalThis.fetch).toHaveBeenCalledWith(
        "/api/ensemble/run?match_id=m1",
        expect.objectContaining({
          method: "POST",
          signal: expect.any(AbortSignal),
        })
      );
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(runEnsemble("m1")).rejects.toThrow("Ensemble run failed: 500");
    });
  });

  // ── getTournamentBracket ──
  describe("getTournamentBracket", () => {
    it("calls /api/tournament/bracket", async () => {
      mockFetchResponse({ round_of_32: [], round_of_16: [], quarter_final: [], semi_final: [], third_place: [], final: [] });

      const result = await getTournamentBracket();
      expect(result.round_of_32).toEqual([]);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getTournamentBracket()).rejects.toThrow("Tournament bracket failed: 500");
    });
  });

  // ── getTournamentProjections ──
  describe("getTournamentProjections", () => {
    it("calls /api/tournament/projections", async () => {
      mockFetchResponse({ projections: [] });

      const result = await getTournamentProjections();
      expect(result.projections).toEqual([]);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getTournamentProjections()).rejects.toThrow("Tournament projections failed: 500");
    });
  });

  // ── getErrorAttributionSummary ──
  describe("getErrorAttributionSummary", () => {
    it("returns counts from top level when API returns { counts }", async () => {
      const counts = {
        draw_underestimated: 5,
        favorite_overestimated: 3,
        underdog_underestimated: 2,
        overconfident_wrong: 1,
        low_score_draw_missed: 4,
        market_missing: 0,
        ai_missing: 0,
        ensemble_helped: 6,
        ensemble_hurt: 2,
      };
      mockFetchResponse({ total_scored: 10, counts, rates: {} });

      const result = await getErrorAttributionSummary();
      expect(result).toEqual(counts);
    });

    it("returns data directly when no counts wrapper", async () => {
      const data = {
        draw_underestimated: 5,
        favorite_overestimated: 3,
        underdog_underestimated: 2,
        overconfident_wrong: 1,
        low_score_draw_missed: 4,
        market_missing: 0,
        ai_missing: 0,
        ensemble_helped: 6,
        ensemble_hurt: 2,
      };
      mockFetchResponse(data);

      const result = await getErrorAttributionSummary();
      expect(result).toEqual(data);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getErrorAttributionSummary()).rejects.toThrow("Failed to fetch error attribution summary");
    });
  });

  // ── getModelComparison ──
  describe("getModelComparison", () => {
    it("calls /api/model-comparison and returns data", async () => {
      const mockData = { comparison: [], sample_sufficient: false, sample_count: 0 };
      mockFetchResponse(mockData);

      const result = await getModelComparison();
      expect(result).toEqual(mockData);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getModelComparison()).rejects.toThrow("Failed to fetch model comparison");
    });
  });

  // ── getMatchCountBreakdown ──
  describe("getMatchCountBreakdown", () => {
    it("calls /api/match-count-breakdown and returns data", async () => {
      const mockData = { total_finished: 10, has_pre_match_prediction: 8, has_pre_kickoff_snapshot: 7, has_locked_snapshot: 6, has_fallback_snapshot: 1, actually_scored: 5, missing_snapshot: 2, details: [] };
      mockFetchResponse(mockData);

      const result = await getMatchCountBreakdown();
      expect(result).toEqual(mockData);
    });

    it("throws on non-ok response", async () => {
      mockFetchResponse(null, false, 500);
      await expect(getMatchCountBreakdown()).rejects.toThrow("Failed to fetch match count breakdown");
    });
  });
});
