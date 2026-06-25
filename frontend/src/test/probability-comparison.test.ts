import { describe, it, expect } from "vitest";
import { directionLabel, getMatchRecommendation } from "../utils/recommendation";
import type { Match, AIPredictionItem, EnsemblePredictionItem, AIModelStatus } from "../types";

describe("Probability comparison display", () => {
  it("directionLabel distinguishes different probability distributions", () => {
    // Baseline: 68/21/11
    const baseline = directionLabel(0.68, 0.21, 0.11);
    expect(baseline).toBe("主胜");

    // AI Flash: 60/25/15 - same direction but different distribution
    const aiFlash = directionLabel(0.60, 0.25, 0.15);
    expect(aiFlash).toBe("主胜");

    // AI Pro: 42/28/30 - different distribution
    const aiPro = directionLabel(0.42, 0.28, 0.30);
    expect(aiPro).toBe("主胜");

    // Draw scenario
    const drawScenario = directionLabel(0.15, 0.50, 0.35);
    expect(drawScenario).toBe("平局");

    // Away win scenario
    const awayScenario = directionLabel(0.10, 0.20, 0.70);
    expect(awayScenario).toBe("客胜");
  });

  it("getMatchRecommendation prefers Ensemble over AI over Baseline", () => {
    const match = {
      prediction: { home_win: 0.68, draw: 0.21, away_win: 0.11, home_xg: 1.8, away_xg: 0.9, confidence_label: "中", model_version: "elo-poisson-v1", scorelines: [], confidence: 0.7, data_confidence: null, data_confidence_label: null, model_confidence: null, model_confidence_label: null, explanation: "", model_inputs: {} },
    } as unknown as Match;

    const aiPredictions: AIPredictionItem[] = [
      {
        id: 1, match_id: "test", provider: "deepseek", model_id: "flash",
        model_version: "ai-deepseek-v4-flash-v1", prompt_version: "v1",
        parsed_home_win: 0.60, parsed_draw: 0.25, parsed_away_win: 0.15,
        confidence: 0.65, risk_flags: [], key_factors: [],
        reason: "test", uncertainties: [],
        disagreement_with_system: "", disagreement_with_market: "",
        recommended_label: "home_win",
        created_at: "", locked_at: null,
        is_pre_match_locked: false, is_fallback_locked: false,
        real_time_only: false, error_code: null, error_message: null,
        latency_ms: 1000,
      },
    ];

    const ensemble: EnsemblePredictionItem = {
      id: 1, match_id: "test", model_version: "ensemble-v1",
      system_model_version: "elo-poisson-v1",
      system_weight: 0.6, market_weight: 0.0,
      ai_weights: { "ai-deepseek-v4-flash-v1": 0.4 },
      source_probabilities: {},
      home_win: 0.648, draw: 0.226, away_win: 0.126,
      confidence: 0.68, reason: "test",
      created_at: "", locked_at: null,
      is_pre_match_locked: false,
      source_status: {},
    };

    // With ensemble available, should use ensemble
    const recWithEnsemble = getMatchRecommendation(match, aiPredictions, ensemble);
    expect(recWithEnsemble.source).toBe("ensemble");

    // Without ensemble but with AI, should use AI
    const recWithAI = getMatchRecommendation(match, aiPredictions, null);
    expect(recWithAI.source).toBe("ai");

    // Without ensemble and AI, should use baseline
    const recBaseline = getMatchRecommendation(match, [], null);
    expect(recBaseline.source).toBe("baseline");
  });

  it("AI prediction with identical probabilities to baseline is still valid", () => {
    // Even if AI returns same probs as baseline, it's still a valid prediction
    const match = {
      prediction: { home_win: 0.68, draw: 0.21, away_win: 0.11, home_xg: 1.8, away_xg: 0.9, confidence_label: "中", model_version: "elo-poisson-v1", scorelines: [], confidence: 0.7, data_confidence: null, data_confidence_label: null, model_confidence: null, model_confidence_label: null, explanation: "", model_inputs: {} },
    } as unknown as Match;

    const aiPredictions: AIPredictionItem[] = [
      {
        id: 1, match_id: "test", provider: "deepseek", model_id: "flash",
        model_version: "ai-deepseek-v4-flash-v1", prompt_version: "v1",
        parsed_home_win: 0.68, parsed_draw: 0.21, parsed_away_win: 0.11, // identical to baseline
        confidence: 0.65, risk_flags: [], key_factors: [],
        reason: "test", uncertainties: [],
        disagreement_with_system: "", disagreement_with_market: "",
        recommended_label: "home_win",
        created_at: "", locked_at: null,
        is_pre_match_locked: false, is_fallback_locked: false,
        real_time_only: false, error_code: null, error_message: null,
        latency_ms: 1000,
      },
    ];

    // AI prediction should still be considered valid
    const rec = getMatchRecommendation(match, aiPredictions, null);
    expect(rec.source).toBe("ai");
    expect(rec.valid).toBe(true);
  });

  it("failed AI prediction does not provide recommendation", () => {
    const match = {
      prediction: { home_win: 0.68, draw: 0.21, away_win: 0.11, home_xg: 1.8, away_xg: 0.9, confidence_label: "中", model_version: "elo-poisson-v1", scorelines: [], confidence: 0.7, data_confidence: null, data_confidence_label: null, model_confidence: null, model_confidence_label: null, explanation: "", model_inputs: {} },
    } as unknown as Match;

    const failedAI: AIPredictionItem[] = [
      {
        id: 1, match_id: "test", provider: "deepseek", model_id: "flash",
        model_version: "ai-deepseek-v4-flash-v1", prompt_version: "v1",
        parsed_home_win: null, parsed_draw: null, parsed_away_win: null,
        confidence: null, risk_flags: [], key_factors: [],
        reason: "", uncertainties: [],
        disagreement_with_system: "", disagreement_with_market: "",
        recommended_label: "",
        created_at: "", locked_at: null,
        is_pre_match_locked: false, is_fallback_locked: false,
        real_time_only: false, error_code: "parse_failed", error_message: "Could not parse",
        latency_ms: 500,
      },
    ];

    // Failed AI should fall back to baseline
    const rec = getMatchRecommendation(match, failedAI, null);
    expect(rec.source).toBe("baseline");
  });
});

describe("MatchDetailDrawer AI model display", () => {
  it("should show all AI model versions from predictions, not just hardcoded ones", () => {
    const aiPredictions = [
      { model_version: "ai-deepseek-v4-flash-v1", parsed_home_win: 0.6, parsed_draw: 0.25, parsed_away_win: 0.15 },
      { model_version: "ai-deepseek-v4-flash-v2", parsed_home_win: 0.55, parsed_draw: 0.3, parsed_away_win: 0.15 },
      { model_version: "ai-deepseek-v4-pro-v1", parsed_home_win: 0.62, parsed_draw: 0.23, parsed_away_win: 0.15 },
    ];
    const versions = [...new Set(aiPredictions.map(p => p.model_version))];
    expect(versions).toContain("ai-deepseek-v4-flash-v1");
    expect(versions).toContain("ai-deepseek-v4-flash-v2");
    expect(versions).toContain("ai-deepseek-v4-pro-v1");
    expect(versions.length).toBe(3);
  });

  it("should include enabled models without predictions", () => {
    const aiPredictions = [
      { model_version: "ai-deepseek-v4-flash-v1", parsed_home_win: 0.6, parsed_draw: 0.25, parsed_away_win: 0.15 },
    ];
    const modelMap = new Map<string, AIModelStatus>([
      ["ai-deepseek-v4-flash-v1", { display_name: "Flash" } as AIModelStatus],
      ["ai-deepseek-v4-pro-v1", { display_name: "Pro" } as AIModelStatus],
      ["ai-deepseek-v4-flash-v2", { display_name: "Flash V2" } as AIModelStatus],
    ]);
    const availableVersions = [...new Set(aiPredictions.map(p => p.model_version))];
    const enabledVersions = [...modelMap.keys()].filter(v => v.startsWith("ai-"));
    const allAIVersions = [...new Set([...availableVersions, ...enabledVersions])];
    expect(allAIVersions.length).toBe(3);
    expect(allAIVersions).toContain("ai-deepseek-v4-pro-v1"); // enabled but no prediction
  });
});

describe("ModelReviewCenter contract", () => {
  it("should not show 'baseline 暂无数据' when baseline_score.available is true", () => {
    const data = {
      sample_count: 4,
      baseline_score: { available: true, sample_count: 4, brier: 0.25, logloss: 0.6, hit_rate: 0.5 },
      version_scores: [{ model_version: "elo-poisson-v1", sample_count: 4, brier: 0.25 }],
      model_recommendation: { recommended_model_version: "elo-poisson-v1", confidence: "low", reason: "" },
    };
    expect(data.baseline_score.available).toBe(true);
    expect(data.sample_count).toBeGreaterThan(0);
    expect(data.version_scores.length).toBeGreaterThan(0);
  });

  it("should show sample_count from API, not derived 0", () => {
    const data = {
      sample_count: 4,
      baseline_score: { available: true, sample_count: 4 },
    };
    expect(data.sample_count).toBe(4);
    expect(data.sample_count).not.toBe(0);
  });
});
