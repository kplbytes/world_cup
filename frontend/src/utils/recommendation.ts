import type { Match, AIPredictionItem, AIPredictionSummary, EnsemblePredictionItem, EnsemblePredictionSummary, Scoreline } from "../types";

// ── Types ──────────────────────────────────────────────────────────

export type RecommendationSource = "ensemble" | "ai" | "baseline" | "none";

export type MatchRecommendation = {
  /** 推荐来源 */
  source: RecommendationSource;
  /** 推荐方向：主胜 / 平局 / 客胜 */
  label: string;
  /** 主胜概率 (0-1) */
  homeWin: number;
  /** 平局概率 (0-1) */
  draw: number;
  /** 客胜概率 (0-1) */
  awayWin: number;
  /** 来源模型版本 */
  modelVersion: string;
  /** 是否有效 */
  valid: boolean;
};

// ── Helpers ────────────────────────────────────────────────────────

export function directionLabel(homeWin: number, draw: number, awayWin: number): string {
  const max = Math.max(homeWin, draw, awayWin);
  if (max === homeWin) return "主胜";
  if (max === draw) return "平局";
  return "客胜";
}

function directionLabelWithNames(
  homeWin: number,
  draw: number,
  awayWin: number,
  homeName: string,
  awayName: string
): string {
  const max = Math.max(homeWin, draw, awayWin);
  if (max === homeWin) return `${homeName}胜`;
  if (max === draw) return "平局";
  return `${awayName}胜`;
}

function isValidProb(v: number | null | undefined): v is number {
  return v != null && isFinite(v) && v >= 0 && v <= 1;
}

function hasValidAI(aiPredictions: AIPredictionItem[]): AIPredictionItem | null {
  // 找到第一个有效（无错误、概率合法）的 AI 预测
  for (const pred of aiPredictions) {
    if (pred.error_message || pred.error_code) continue;
    if (isValidProb(pred.parsed_home_win) && isValidProb(pred.parsed_draw) && isValidProb(pred.parsed_away_win)) {
      return pred;
    }
  }
  return null;
}

// ── Core Function ──────────────────────────────────────────────────

/**
 * 统一推荐来源选择逻辑。
 *
 * 优先级：
 * 1. 有 Ensemble 且有效 → 显示 Ensemble 推荐
 * 2. 无 Ensemble 但有有效 AI → 显示 AI 推荐
 * 3. 无 AI 但有 Baseline → 显示 Baseline 推荐
 * 4. 都没有 → 才显示待生成
 */
export function getMatchRecommendation(
  match: Match,
  aiPredictions?: AIPredictionItem[] | AIPredictionSummary | null,
  ensemble?: EnsemblePredictionItem | EnsemblePredictionSummary | null
): MatchRecommendation {
  const baseline = match.prediction;

  // 1. Ensemble 优先
  if (ensemble && isValidProb(ensemble.home_win) && isValidProb(ensemble.draw) && isValidProb(ensemble.away_win)) {
    return {
      source: "ensemble",
      label: directionLabel(ensemble.home_win, ensemble.draw, ensemble.away_win),
      homeWin: ensemble.home_win,
      draw: ensemble.draw,
      awayWin: ensemble.away_win,
      modelVersion: ensemble.model_version,
      valid: true,
    };
  }

  // 2. 有效 AI
  const validAI = getValidAIFromInput(aiPredictions);
  if (validAI) {
    return {
      source: "ai",
      label: directionLabel(validAI.home_win, validAI.draw, validAI.away_win),
      homeWin: validAI.home_win,
      draw: validAI.draw,
      awayWin: validAI.away_win,
      modelVersion: validAI.model_version,
      valid: true,
    };
  }

  // 3. Baseline
  if (baseline && isValidProb(baseline.home_win) && isValidProb(baseline.draw) && isValidProb(baseline.away_win)) {
    return {
      source: "baseline",
      label: directionLabel(baseline.home_win, baseline.draw, baseline.away_win),
      homeWin: baseline.home_win,
      draw: baseline.draw,
      awayWin: baseline.away_win,
      modelVersion: baseline.model_version,
      valid: true,
    };
  }

  // 4. 无任何预测
  return {
    source: "none",
    label: "待生成",
    homeWin: 0,
    draw: 0,
    awayWin: 0,
    modelVersion: "",
    valid: false,
  };
}

/** Extract valid AI prediction from either full AIPredictionItem[] or AIPredictionSummary */
function getValidAIFromInput(
  aiPredictions?: AIPredictionItem[] | AIPredictionSummary | null
): { home_win: number; draw: number; away_win: number; model_version: string } | null {
  if (!aiPredictions) return null;
  // Summary object (not an array)
  if (!Array.isArray(aiPredictions)) {
    const s = aiPredictions;
    if (isValidProb(s.home_win) && isValidProb(s.draw) && isValidProb(s.away_win)) {
      return s;
    }
    return null;
  }
  // Full AIPredictionItem array
  const valid = hasValidAI(aiPredictions);
  if (valid) {
    return { home_win: valid.parsed_home_win!, draw: valid.parsed_draw!, away_win: valid.parsed_away_win!, model_version: valid.model_version };
  }
  return null;
}

/**
 * 带队名的推荐标签（用于卡片显示）
 */
export function getMatchRecommendationLabel(
  rec: MatchRecommendation,
  homeName: string,
  awayName: string
): string {
  if (!rec.valid) return "待生成";
  return directionLabelWithNames(rec.homeWin, rec.draw, rec.awayWin, homeName, awayName);
}

/**
 * 推荐来源的中文名称
 */
export function getSourceDisplayName(source: RecommendationSource): string {
  switch (source) {
    case "ensemble": return "Ensemble";
    case "ai": return "AI";
    case "baseline": return "Baseline";
    case "none": return "";
  }
}

/**
 * 根据推荐方向过滤比分列表，确保"比分倾向"与推荐方向一致。
 *
 * 当推荐来源为 Ensemble/AI 时，Baseline 的最高概率比分可能与推荐方向不同
 * （如 Baseline 最高概率比分是 1-1 平局，但 Ensemble 认为客胜概率最高），
 * 导致"比分倾向"与"推荐"矛盾。此函数按推荐方向过滤比分，只保留方向一致的比分。
 */
export function filterScorelinesByDirection(
  scorelines: Scoreline[],
  rec: MatchRecommendation
): Scoreline[] {
  if (!rec.valid || scorelines.length === 0) return scorelines;

  const direction = rec.label;
  const filtered = scorelines.filter((s) => {
    if (direction === "主胜") return s.home_goals > s.away_goals;
    if (direction === "平局") return s.home_goals === s.away_goals;
    if (direction === "客胜") return s.home_goals < s.away_goals;
    return true;
  });

  // 如果过滤后为空（极端情况），回退到原始列表
  return filtered.length > 0 ? filtered : scorelines;
}
