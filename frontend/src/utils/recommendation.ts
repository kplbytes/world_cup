import type { Match, AIPredictionItem, EnsemblePredictionItem } from "../types";

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
  aiPredictions?: AIPredictionItem[],
  ensemble?: EnsemblePredictionItem | null
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
  const validAI = aiPredictions ? hasValidAI(aiPredictions) : null;
  if (validAI) {
    return {
      source: "ai",
      label: directionLabel(validAI.parsed_home_win!, validAI.parsed_draw!, validAI.parsed_away_win!),
      homeWin: validAI.parsed_home_win!,
      draw: validAI.parsed_draw!,
      awayWin: validAI.parsed_away_win!,
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
