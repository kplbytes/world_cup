import { describe, it, expect } from "vitest";
import { filterScorelinesByDirection } from "../utils/recommendation";
import type { Scoreline } from "../types";
import type { MatchRecommendation } from "../utils/recommendation";

// ── filterScorelinesByDirection ──────────────────────────────────────

describe("filterScorelinesByDirection", () => {
  const scorelines: Scoreline[] = [
    { home_goals: 2, away_goals: 0, probability: 0.15 },
    { home_goals: 1, away_goals: 0, probability: 0.12 },
    { home_goals: 1, away_goals: 1, probability: 0.10 },
    { home_goals: 0, away_goals: 1, probability: 0.08 },
    { home_goals: 0, away_goals: 2, probability: 0.05 },
  ];

  it("filters to home wins when direction is 主胜", () => {
    const rec: MatchRecommendation = {
      source: "baseline",
      label: "主胜",
      homeWin: 0.5,
      draw: 0.3,
      awayWin: 0.2,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    expect(result.every((s) => s.home_goals > s.away_goals)).toBe(true);
    expect(result).toHaveLength(2);
  });

  it("filters to draws when direction is 平局", () => {
    const rec: MatchRecommendation = {
      source: "ensemble",
      label: "平局",
      homeWin: 0.25,
      draw: 0.5,
      awayWin: 0.25,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    expect(result.every((s) => s.home_goals === s.away_goals)).toBe(true);
    expect(result).toHaveLength(1);
  });

  it("filters to away wins when direction is 客胜", () => {
    const rec: MatchRecommendation = {
      source: "ai",
      label: "客胜",
      homeWin: 0.2,
      draw: 0.3,
      awayWin: 0.5,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    expect(result.every((s) => s.home_goals < s.away_goals)).toBe(true);
    expect(result).toHaveLength(2);
  });

  it("returns original list when rec is invalid", () => {
    const rec: MatchRecommendation = {
      source: "none",
      label: "待生成",
      homeWin: 0,
      draw: 0,
      awayWin: 0,
      modelVersion: "",
      valid: false,
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    expect(result).toEqual(scorelines);
  });

  it("returns original list when scorelines is empty", () => {
    const rec: MatchRecommendation = {
      source: "baseline",
      label: "主胜",
      homeWin: 0.5,
      draw: 0.3,
      awayWin: 0.2,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection([], rec);
    expect(result).toEqual([]);
  });

  it("falls back to original list when filter result is empty", () => {
    // All home wins, but direction is 客胜
    const homeOnlyScorelines: Scoreline[] = [
      { home_goals: 2, away_goals: 0, probability: 0.15 },
      { home_goals: 1, away_goals: 0, probability: 0.12 },
    ];
    const rec: MatchRecommendation = {
      source: "ai",
      label: "客胜",
      homeWin: 0.2,
      draw: 0.3,
      awayWin: 0.5,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(homeOnlyScorelines, rec);
    // Falls back to original since filter result would be empty
    expect(result).toEqual(homeOnlyScorelines);
  });

  it("preserves probability order in filtered results", () => {
    const rec: MatchRecommendation = {
      source: "baseline",
      label: "主胜",
      homeWin: 0.5,
      draw: 0.3,
      awayWin: 0.2,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    // Results should maintain original order
    expect(result[0].probability).toBe(0.15);
    expect(result[1].probability).toBe(0.12);
  });

  it("handles high-scoring scorelines correctly", () => {
    const highScorelines: Scoreline[] = [
      { home_goals: 5, away_goals: 3, probability: 0.02 },
      { home_goals: 3, away_goals: 3, probability: 0.01 },
      { home_goals: 1, away_goals: 4, probability: 0.03 },
    ];
    const rec: MatchRecommendation = {
      source: "ensemble",
      label: "客胜",
      homeWin: 0.15,
      draw: 0.25,
      awayWin: 0.6,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(highScorelines, rec);
    expect(result).toHaveLength(1);
    expect(result[0].home_goals).toBe(1);
    expect(result[0].away_goals).toBe(4);
  });

  it("handles single scoreline list", () => {
    const single: Scoreline[] = [
      { home_goals: 1, away_goals: 0, probability: 0.12 },
    ];
    const rec: MatchRecommendation = {
      source: "baseline",
      label: "主胜",
      homeWin: 0.5,
      draw: 0.3,
      awayWin: 0.2,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(single, rec);
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(single[0]);
  });

  it("returns all scorelines when direction label is unrecognized", () => {
    const rec: MatchRecommendation = {
      source: "none",
      label: "未知方向",
      homeWin: 0,
      draw: 0,
      awayWin: 0,
      modelVersion: "",
      valid: true, // valid but unknown label
    };
    const result = filterScorelinesByDirection(scorelines, rec);
    // Unknown direction falls through the filter conditions → returns all
    expect(result).toHaveLength(scorelines.length);
  });

  it("filters all-draw scorelines for draw direction", () => {
    const drawOnly: Scoreline[] = [
      { home_goals: 0, away_goals: 0, probability: 0.08 },
      { home_goals: 1, away_goals: 1, probability: 0.12 },
      { home_goals: 2, away_goals: 2, probability: 0.05 },
    ];
    const rec: MatchRecommendation = {
      source: "ai",
      label: "平局",
      homeWin: 0.2,
      draw: 0.55,
      awayWin: 0.25,
      modelVersion: "v1",
      valid: true,
    };
    const result = filterScorelinesByDirection(drawOnly, rec);
    expect(result).toHaveLength(3);
    expect(result.every((s) => s.home_goals === s.away_goals)).toBe(true);
  });
});
