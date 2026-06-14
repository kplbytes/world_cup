"""Model experiment runner.

Runs parameter search experiments against historical match data,
evaluating multiple model configurations and outputting results.
"""

from __future__ import annotations

import csv
import math
import sys
from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import poisson

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from app.db import create_database, session_scope
from app.models import MarketSnapshot, Match, PredictionSnapshot, Team
from sqlalchemy import select

_CLIP = 1e-6


@dataclass
class ExperimentResult:
    experiment_id: str
    model_version: str
    config: dict[str, Any]
    sample_count: int
    hit_rate: float
    brier: float
    logloss: float
    draw_brier: float
    favorite_wrong_count: int
    overconfident_wrong_count: int
    upset_miss_count: int
    warning_helped_count: int
    numerical_helped_count: int


def predict_with_config(
    home_strength: float,
    away_strength: float,
    config: dict[str, Any],
) -> dict[str, float]:
    """Predict match probabilities using given config parameters."""
    base_goal_home = config.get("base_goal_mean_home", 1.25)
    base_goal_away = config.get("base_goal_mean_away", 1.10)
    str_coeff_home = config.get("strength_coeff_home", 0.90)
    str_coeff_away = config.get("strength_coeff_away", 0.75)
    min_xg = config.get("min_xg", 0.20)
    max_xg = config.get("max_xg", 3.50)
    draw_boost = config.get("draw_boost", 1.00)
    favorite_dampening = config.get("favorite_dampening", 0.00)
    underdog_boost = config.get("underdog_boost", 0.00)
    upset_factor = config.get("upset_factor", 0.00)
    market_blend_weight = config.get("market_blend_weight", 0.00)
    market_probs = config.get("_market_probs")

    strength_delta = home_strength - away_strength
    home_xg = float(np.clip(base_goal_home + str_coeff_home * strength_delta, min_xg, max_xg))
    away_xg = float(np.clip(base_goal_away - str_coeff_away * strength_delta, min_xg, max_xg))

    home_goals = _goal_probs(home_xg)
    away_goals = _goal_probs(away_xg)
    matrix = np.outer(home_goals, away_goals)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())
    total = home_win + draw + away_win
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Draw boost
    if draw_boost != 1.0 and draw > 0:
        draw_boosted = draw * draw_boost
        excess = draw_boosted - draw
        ha_sum = home_win + away_win
        if ha_sum > 0:
            home_win -= excess * (home_win / ha_sum)
            away_win -= excess * (away_win / ha_sum)
        draw = draw_boosted
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Favorite dampening
    if favorite_dampening > 0:
        probs = [home_win, draw, away_win]
        max_idx = probs.index(max(probs))
        uniform = 1.0 / 3.0
        excess = probs[max_idx] - uniform
        if excess > 0:
            reduction = excess * favorite_dampening
            probs[max_idx] -= reduction
            others = [i for i in range(3) if i != max_idx]
            other_sum = sum(probs[i] for i in others)
            if other_sum > 0:
                for i in others:
                    probs[i] += reduction * (probs[i] / other_sum)
            home_win, draw, away_win = probs
            total = home_win + draw + away_win
            home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Underdog boost
    if underdog_boost > 0:
        if home_win <= away_win:
            home_win += underdog_boost
        else:
            away_win += underdog_boost
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Upset factor
    if upset_factor > 0:
        if home_win <= away_win:
            home_win += upset_factor
        else:
            away_win += upset_factor
        total = home_win + draw + away_win
        home_win, draw, away_win = home_win / total, draw / total, away_win / total

    # Market blend
    if market_blend_weight > 0 and market_probs:
        k_home, k_draw, k_away = market_probs
        b_home = (1 - market_blend_weight) * home_win + market_blend_weight * k_home
        b_draw = (1 - market_blend_weight) * draw + market_blend_weight * k_draw
        b_away = (1 - market_blend_weight) * away_win + market_blend_weight * k_away
        total = b_home + b_draw + b_away
        home_win, draw, away_win = b_home / total, b_draw / total, b_away / total

    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def _goal_probs(expected_goals: float) -> np.ndarray:
    exact = poisson.pmf(np.arange(8), expected_goals)
    tail = max(0.0, 1.0 - float(exact.sum()))
    values = np.append(exact, tail)
    return values / values.sum()


def _compute_brier(probs: dict[str, float], actual_result: str) -> float:
    o_home = 1.0 if actual_result == "home" else 0.0
    o_draw = 1.0 if actual_result == "draw" else 0.0
    o_away = 1.0 if actual_result == "away" else 0.0
    return (probs["home_win"] - o_home) ** 2 + (probs["draw"] - o_draw) ** 2 + (probs["away_win"] - o_away) ** 2


def _compute_logloss(probs: dict[str, float], actual_result: str) -> float:
    o_home = 1.0 if actual_result == "home" else 0.0
    o_draw = 1.0 if actual_result == "draw" else 0.0
    o_away = 1.0 if actual_result == "away" else 0.0
    ch = max(_CLIP, min(1 - _CLIP, probs["home_win"]))
    cd = max(_CLIP, min(1 - _CLIP, probs["draw"]))
    ca = max(_CLIP, min(1 - _CLIP, probs["away_win"]))
    return -(o_home * math.log(ch) + o_draw * math.log(cd) + o_away * math.log(ca))


def run_experiments(db_path: str | Path | None = None) -> list[ExperimentResult]:
    """Run parameter search experiments against historical data."""
    from app.config import settings

    if db_path:
        create_database(db_path)

    # Search grid
    draw_boost_values = [1.00, 1.05, 1.10, 1.15]
    favorite_dampening_values = [0.00, 0.05, 0.10]
    underdog_boost_values = [0.00, 0.03, 0.06]
    market_blend_weight_values = [0.00, 0.10, 0.20]
    numerical_adjustment_weight_values = [0.00, 0.25, 0.50, 1.00]

    # Load historical data
    with session_scope() as session:
        # Get team Elo ratings
        teams = list(session.scalars(select(Team)))
        from app.models import TeamRating
        ratings = {}
        by_team: dict[str, list[TeamRating]] = {}
        for rating in session.scalars(select(TeamRating).order_by(TeamRating.team_id, TeamRating.effective_date.desc())):
            by_team.setdefault(rating.team_id, []).append(rating)
        for team in teams:
            if team.id in by_team:
                ratings[team.id] = by_team[team.id][0].elo
            else:
                ratings[team.id] = 1500.0

        # Normalize strengths
        min_elo = min(ratings.values())
        max_elo = max(ratings.values())
        spread = max_elo - min_elo or 1.0
        strengths = {tid: (elo - min_elo) / spread for tid, elo in ratings.items()}

        # Get final matches
        matches = list(session.scalars(select(Match).where(Match.status == "final")))
        if not matches:
            print("No final matches found for experiments.")
            return []

        # Get market snapshots
        market_snaps = {
            row.match_id: row
            for row in session.scalars(select(MarketSnapshot).where(MarketSnapshot.provider == "sporttery"))
        }

    # Run experiments
    results: list[ExperimentResult] = []
    exp_id = 0

    for db, fd, ub, mbw, naw in product(
        draw_boost_values, favorite_dampening_values, underdog_boost_values,
        market_blend_weight_values, numerical_adjustment_weight_values,
    ):
        exp_id += 1
        config = {
            "draw_boost": db,
            "favorite_dampening": fd,
            "underdog_boost": ub,
            "market_blend_weight": mbw,
            "numerical_adjustment_weight": naw,
        }

        brier_sum = 0.0
        logloss_sum = 0.0
        draw_brier_sum = 0.0
        draw_count = 0
        hit_count = 0
        favorite_wrong = 0
        overconfident_wrong = 0
        upset_miss = 0

        for match in matches:
            hs = strengths.get(match.home_team_id, 0.5)
            aws = strengths.get(match.away_team_id, 0.5)

            # Add market probs if blending
            cfg_with_market = dict(config)
            if mbw > 0:
                ms = market_snaps.get(match.id)
                if ms:
                    cfg_with_market["_market_probs"] = (ms.home_probability, ms.draw_probability, ms.away_probability)

            probs = predict_with_config(hs, aws, cfg_with_market)

            actual_home = match.home_score or 0
            actual_away = match.away_score or 0
            if actual_home > actual_away:
                actual_result = "home"
            elif actual_home == actual_away:
                actual_result = "draw"
            else:
                actual_result = "away"

            predicted_outcome = max(probs, key=probs.get)
            outcome_correct = predicted_outcome == actual_result

            brier = _compute_brier(probs, actual_result)
            logloss = _compute_logloss(probs, actual_result)

            brier_sum += brier
            logloss_sum += logloss
            if outcome_correct:
                hit_count += 1
            if actual_result == "draw":
                draw_brier_sum += brier
                draw_count += 1
            max_prob = max(probs.values())
            if not outcome_correct and max_prob >= 0.50:
                favorite_wrong += 1
            if not outcome_correct and max_prob >= 0.60:
                overconfident_wrong += 1
            if not outcome_correct and actual_result != "draw" and min(probs["home_win"], probs["away_win"]) < 0.30:
                upset_miss += 1

        n = len(matches)
        version_name = f"exp-{db:.2f}-{fd:.2f}-{ub:.2f}-{mbw:.2f}-{naw:.2f}"

        results.append(ExperimentResult(
            experiment_id=f"E{exp_id:04d}",
            model_version=version_name,
            config=config,
            sample_count=n,
            hit_rate=hit_count / n,
            brier=brier_sum / n,
            logloss=logloss_sum / n,
            draw_brier=draw_brier_sum / max(draw_count, 1),
            favorite_wrong_count=favorite_wrong,
            overconfident_wrong_count=overconfident_wrong,
            upset_miss_count=upset_miss,
            warning_helped_count=0,
            numerical_helped_count=0,
        ))

    return results


def write_results(results: list[ExperimentResult], artifacts_dir: Path) -> None:
    """Write experiment results to CSV, markdown, and best config YAML."""
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Sort by Brier
    results_sorted = sorted(results, key=lambda r: r.brier)

    # CSV
    csv_path = artifacts_dir / "model_experiments.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "experiment_id", "model_version", "draw_boost", "favorite_dampening",
            "underdog_boost", "market_blend_weight", "numerical_adjustment_weight",
            "sample_count", "hit_rate", "brier", "logloss", "draw_brier",
            "favorite_wrong_count", "overconfident_wrong_count", "upset_miss_count",
        ])
        for r in results_sorted:
            writer.writerow([
                r.experiment_id, r.model_version,
                r.config.get("draw_boost", 1.0),
                r.config.get("favorite_dampening", 0.0),
                r.config.get("underdog_boost", 0.0),
                r.config.get("market_blend_weight", 0.0),
                r.config.get("numerical_adjustment_weight", 0.0),
                r.sample_count, f"{r.hit_rate:.4f}", f"{r.brier:.4f}",
                f"{r.logloss:.4f}", f"{r.draw_brier:.4f}",
                r.favorite_wrong_count, r.overconfident_wrong_count, r.upset_miss_count,
            ])

    # Markdown
    md_path = artifacts_dir / "model_experiments.md"
    lines = [
        "# 模型参数实验结果",
        "",
        f"共 {len(results)} 组实验配置",
        "",
        "## Top 10 最佳配置 (按 Brier 排序)",
        "",
        "| 排名 | draw_boost | fav_damp | underdog | market | numeric | hit_rate | Brier | LogLoss | draw_brier | fav_wrong | overconf_wrong |",
        "|------|-----------|---------|---------|--------|---------|---------|-------|---------|-----------|----------|---------------|",
    ]
    for i, r in enumerate(results_sorted[:10], 1):
        lines.append(
            f"| {i} | {r.config.get('draw_boost', 1.0):.2f} | "
            f"{r.config.get('favorite_dampening', 0.0):.2f} | "
            f"{r.config.get('underdog_boost', 0.0):.2f} | "
            f"{r.config.get('market_blend_weight', 0.0):.2f} | "
            f"{r.config.get('numerical_adjustment_weight', 0.0):.2f} | "
            f"{r.hit_rate:.1%} | {r.brier:.4f} | {r.logloss:.4f} | "
            f"{r.draw_brier:.4f} | {r.favorite_wrong_count} | {r.overconfident_wrong_count} |"
        )
    lines.append("")
    lines.append(f"完整结果见: {csv_path.name}")

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    # Best config YAML
    best = results_sorted[0] if results_sorted else None
    if best:
        import yaml
        yaml_path = artifacts_dir / "best_model_config.yaml"
        best_config = {
            "best_experiment_id": best.experiment_id,
            "model_version": best.model_version,
            "config": best.config,
            "metrics": {
                "sample_count": best.sample_count,
                "hit_rate": round(best.hit_rate, 4),
                "brier": round(best.brier, 4),
                "logloss": round(best.logloss, 4),
                "draw_brier": round(best.draw_brier, 4),
                "favorite_wrong_count": best.favorite_wrong_count,
                "overconfident_wrong_count": best.overconfident_wrong_count,
            },
        }
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(best_config, f, allow_unicode=True, default_flow_style=False)

    print(f"Results written to {artifacts_dir}")
    print(f"  - {csv_path.name}")
    print(f"  - {md_path.name}")
    if best:
        print(f"Best config: draw_boost={best.config.get('draw_boost')}, "
              f"favorite_dampening={best.config.get('favorite_dampening')}, "
              f"underdog_boost={best.config.get('underdog_boost')}, "
              f"market_blend_weight={best.config.get('market_blend_weight')}, "
              f"Brier={best.brier:.4f}")


if __name__ == "__main__":
    artifacts = PROJECT_ROOT.parent / "artifacts"
    results = run_experiments()
    if results:
        write_results(results, artifacts)
