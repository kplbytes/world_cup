import { describe, it, expect } from "vitest";
import {
  directionLabel,
  getMatchRecommendation,
  getSourceDisplayName,
  getMatchRecommendationLabel,
} from "../utils/recommendation";
import type { Match, AIPredictionItem, EnsemblePredictionItem } from "../types";

// ── directionLabel ──────────────────────────────────────────────────

describe("directionLabel", () => {
  it("returns 主胜 when homeWin is highest", () => {
    expect(directionLabel(0.7, 0.2, 0.1)).toBe("主胜");
  });

  it("returns 平局 when draw is highest", () => {
    expect(directionLabel(0.1, 0.6, 0.3)).toBe("平局");
  });

  it("returns 客胜 when awayWin is highest", () => {
    expect(directionLabel(0.1, 0.2, 0.7)).toBe("客胜");
  });
});

// ── getMatchRecommendation ──────────────────────────────────────────

function makeMatch(overrides: Partial<Match> = {}): Match {
  return {
    id: "test-match",
    group_code: "A",
    kickoff: "2026-06-14T10:00:00Z",
    venue: "Test Stadium",
    status: "scheduled",
    home_team: { id: "H1", name: "Home", short_name: "Home", flag: "⚽" },
    away_team: { id: "A1", name: "Away", short_name: "Away", flag: "⚽" },
    home_score: null,
    away_score: null,
    manual_adjustments: [],
    source: "test",
    source_updated_at: "2026-06-14T00:00:00Z",
    market: null,
    prediction: {
      home_xg: 1.4,
      away_xg: 1.0,
      home_win: 0.5,
      draw: 0.3,
      away_win: 0.2,
      scorelines: [],
      confidence: 0.8,
      confidence_label: "高",
      data_confidence: 0.85,
      data_confidence_label: "高",
      model_confidence: 0.22,
      model_confidence_label: "中",
      explanation: "",
      model_inputs: { home_elo: 1700, away_elo: 1600 },
      model_version: "elo-poisson-v1",
    },
    ...overrides,
  } as Match;
}

describe("getMatchRecommendation", () => {
  it("returns source=ensemble when ensemble data is valid", () => {
    const match = makeMatch();
    const ensemble: EnsemblePredictionItem = {
      id: 1,
      match_id: "test-match",
      model_version: "ensemble-v1",
      system_model_version: "elo-poisson-v1",
      home_win: 0.6,
      draw: 0.25,
      away_win: 0.15,
      system_weight: 0.4,
      market_weight: 0.3,
      ai_weights: {},
      source_probabilities: {},
      confidence: 0.8,
      source_status: {},
      reason: "",
      created_at: "2026-06-14T00:00:00Z",
      locked_at: null,
      is_pre_match_locked: false,
    };
    const rec = getMatchRecommendation(match, [], ensemble);
    expect(rec.source).toBe("ensemble");
    expect(rec.label).toBe("主胜");
    expect(rec.valid).toBe(true);
  });

  it("returns source=ai when no ensemble but valid AI exists", () => {
    const match = makeMatch();
    const aiPredictions: AIPredictionItem[] = [
      {
        id: 1,
        match_id: "test-match",
        model_version: "ai-deepseek-v4-flash-v1",
        model_id: "deepseek-flash",
        provider: "deepseek",
        prompt_version: "v1",
        parsed_home_win: 0.55,
        parsed_draw: 0.25,
        parsed_away_win: 0.2,
        confidence: 0.8,
        risk_flags: [],
        key_factors: [],
        reason: "",
        uncertainties: [],
        disagreement_with_system: "",
        disagreement_with_market: "",
        recommended_label: "home_win",
        created_at: "2026-06-14T00:00:00Z",
        locked_at: null,
        is_pre_match_locked: false,
        is_fallback_locked: false,
        real_time_only: false,
        error_code: null,
        error_message: null,
        latency_ms: null,
      },
    ];
    const rec = getMatchRecommendation(match, aiPredictions, null);
    expect(rec.source).toBe("ai");
    expect(rec.valid).toBe(true);
  });

  it("returns source=baseline when no ensemble/AI but baseline exists", () => {
    const match = makeMatch();
    const rec = getMatchRecommendation(match, [], null);
    expect(rec.source).toBe("baseline");
    expect(rec.valid).toBe(true);
  });

  it("returns source=none when no valid predictions", () => {
    const match = makeMatch({ prediction: null } as Partial<Match>);
    const rec = getMatchRecommendation(match, [], null);
    expect(rec.source).toBe("none");
    expect(rec.valid).toBe(false);
    expect(rec.label).toBe("待生成");
  });
});

// ── getSourceDisplayName ────────────────────────────────────────────

describe("getSourceDisplayName", () => {
  it("returns Ensemble for ensemble source", () => {
    expect(getSourceDisplayName("ensemble")).toBe("Ensemble");
  });

  it("returns AI for ai source", () => {
    expect(getSourceDisplayName("ai")).toBe("AI");
  });

  it("returns Baseline for baseline source", () => {
    expect(getSourceDisplayName("baseline")).toBe("Baseline");
  });

  it("returns empty string for none source", () => {
    expect(getSourceDisplayName("none")).toBe("");
  });
});

// ── getMatchRecommendationLabel ─────────────────────────────────────

describe("getMatchRecommendationLabel", () => {
  it("returns 待生成 for invalid recommendation", () => {
    const rec = getMatchRecommendation(makeMatch({ prediction: null } as Partial<Match>), [], null);
    expect(getMatchRecommendationLabel(rec, "Home", "Away")).toBe("待生成");
  });

  it("returns team name label for home win", () => {
    const match = makeMatch();
    const rec = getMatchRecommendation(match, [], null);
    expect(getMatchRecommendationLabel(rec, "巴西", "阿根廷")).toBe("巴西胜");
  });

  it("returns 平局 for draw", () => {
    const match = makeMatch({
      prediction: {
        home_xg: 1.0,
        away_xg: 1.0,
        home_win: 0.25,
        draw: 0.5,
        away_win: 0.25,
        scorelines: [],
        confidence: 0.5,
        confidence_label: "低",
        data_confidence: 0.5,
        data_confidence_label: "低",
        model_confidence: 0.5,
        model_confidence_label: "低",
        explanation: "",
        model_inputs: { home_elo: 1600, away_elo: 1600 },
        model_version: "elo-poisson-v1",
      },
    } as Partial<Match>);
    const rec = getMatchRecommendation(match, [], null);
    expect(getMatchRecommendationLabel(rec, "Home", "Away")).toBe("平局");
  });
});
