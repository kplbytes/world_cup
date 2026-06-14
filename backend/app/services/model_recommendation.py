"""Model recommendation system.

Automatically recommends which model version to use for the next round
based on scoring metrics, sample sizes, and stability criteria.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.scoring import model_score_by_version


def get_model_recommendation(session: Session) -> dict[str, Any]:
    """Generate a model version recommendation for the next round.

    Rules:
    1. Prioritize Brier score (lower = better)
    2. When Brier is close, look at LogLoss
    3. If sample too small, don't auto-switch, just observe
    4. If improvement < 3%, don't recommend switching
    5. If new model has more overconfident_wrong, don't recommend
    6. If version only good on few matches, don't recommend as default
    """
    versions = model_score_by_version(session)

    if not versions:
        return {
            "recommended_model_version": "elo-poisson-v1",
            "reason": "暂无评分数据，使用默认模型",
            "confidence": "low",
            "sample_warning": "没有任何已评分比赛",
            "fallback_model_version": "elo-poisson-v1",
        }

    # Sort by Brier (lower is better)
    versions_sorted = sorted(versions, key=lambda v: (v["sample_count"] == 0, v["brier"]))
    best = versions_sorted[0]

    # Find the baseline (v1 without modifications)
    baseline = None
    for v in versions:
        if v["model_version"] == "elo-poisson-v1":
            baseline = v
            break

    if baseline is None:
        baseline = versions_sorted[0]

    # Check sample size
    min_samples = 5
    sample_warning = ""
    confidence = "low"

    if best["sample_count"] < min_samples:
        sample_warning = f"最佳模型 {best['model_version']} 仅 {best['sample_count']} 场样本，不足以得出可靠结论"
        confidence = "low"
    elif best["sample_count"] < 15:
        sample_warning = f"样本量 {best['sample_count']} 场，统计可靠性有限"
        confidence = "medium"
    else:
        confidence = "high"

    # Check if best is the same as baseline
    if best["model_version"] == baseline["model_version"]:
        return {
            "recommended_model_version": best["model_version"],
            "reason": f"当前基线模型已是最佳(Brier={best['brier']:.4f})",
            "confidence": confidence,
            "sample_warning": sample_warning,
            "fallback_model_version": "elo-poisson-v1",
        }

    # Check improvement threshold
    brier_improvement = baseline["brier"] - best["brier"]
    relative_improvement = brier_improvement / baseline["brier"] if baseline["brier"] > 0 else 0

    if relative_improvement < 0.03:
        return {
            "recommended_model_version": baseline["model_version"],
            "reason": (
                f"{best['model_version']} 的 Brier 略优 "
                f"({best['brier']:.4f} vs {baseline['brier']:.4f})，"
                f"但提升仅 {relative_improvement:.1%}，不足 3%，不建议切换"
            ),
            "confidence": confidence,
            "sample_warning": sample_warning,
            "fallback_model_version": "elo-poisson-v1",
        }

    # Check overconfident_wrong count
    if best.get("overconfident_wrong_count", 0) > baseline.get("overconfident_wrong_count", 0):
        if best["sample_count"] >= baseline["sample_count"]:
            return {
                "recommended_model_version": baseline["model_version"],
                "reason": (
                    f"{best['model_version']} 虽然整体 Brier 更优，"
                    f"但过度自信错误更多({best.get('overconfident_wrong_count', 0)} vs "
                    f"{baseline.get('overconfident_wrong_count', 0)})，不建议切换"
                ),
                "confidence": "medium",
                "sample_warning": sample_warning,
                "fallback_model_version": "elo-poisson-v1",
            }

    # All checks passed - recommend the new version
    return {
        "recommended_model_version": best["model_version"],
        "reason": (
            f"{best['model_version']} 在 {best['sample_count']} 场比赛中 "
            f"Brier={best['brier']:.4f} (vs 基线 {baseline['brier']:.4f})，"
            f"提升 {relative_improvement:.1%}，建议切换"
        ),
        "confidence": confidence,
        "sample_warning": sample_warning,
        "fallback_model_version": "elo-poisson-v1",
        "best_brier": best["brier"],
        "baseline_brier": baseline["brier"],
        "brier_improvement": round(brier_improvement, 4),
        "relative_improvement": round(relative_improvement, 4),
    }
