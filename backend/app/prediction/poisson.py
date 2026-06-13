from dataclasses import dataclass
from math import isfinite

import numpy as np
from scipy.stats import poisson

from app.prediction.confidence import (
    ConfidenceInputs,
    data_confidence,
    model_confidence,
)
from app.prediction.explanation import explain_prediction


MODEL_VERSION = "elo-poisson-v1"
_MAX_EXACT_GOALS = 7


@dataclass(frozen=True)
class MatchContext:
    data_freshness: float
    ranking_coverage: float
    history_coverage: float
    provider_agreement: float
    recent_form_delta: float = 0.0
    host_advantage: float = 0.0
    home_attack_adjustment: float = 0.0
    home_defense_adjustment: float = 0.0
    away_attack_adjustment: float = 0.0
    away_defense_adjustment: float = 0.0
    home_name: str = "主队"
    away_name: str = "客队"


@dataclass(frozen=True)
class ScorelineProbability:
    home_goals: int
    away_goals: int
    probability: float


@dataclass(frozen=True)
class MatchPredictionResult:
    home_xg: float
    away_xg: float
    home_win: float
    draw: float
    away_win: float
    scorelines: list[ScorelineProbability]
    score_matrix: list[list[float]]
    confidence: float
    confidence_label: str
    data_confidence: float
    data_confidence_label: str
    model_confidence: float
    model_confidence_label: str
    explanation: str
    model_version: str


def predict_match(
    home_strength: float,
    away_strength: float,
    context: MatchContext,
) -> MatchPredictionResult:
    if not isfinite(home_strength) or not isfinite(away_strength):
        raise ValueError("team strengths must be finite")

    strength_delta = (
        home_strength
        - away_strength
        + context.recent_form_delta
        + context.host_advantage
    )
    home_xg = float(
        np.clip(
            1.25
            + 0.90 * strength_delta
            + context.home_attack_adjustment
            - context.away_defense_adjustment,
            0.20,
            3.50,
        )
    )
    away_xg = float(
        np.clip(
            1.10
            - 0.75 * strength_delta
            + context.away_attack_adjustment
            - context.home_defense_adjustment,
            0.20,
            3.50,
        )
    )
    home_goals = _goal_probabilities(home_xg)
    away_goals = _goal_probabilities(away_xg)
    matrix = np.outer(home_goals, away_goals)

    home_win = float(np.tril(matrix, k=-1).sum())
    draw = float(np.trace(matrix))
    away_win = float(np.triu(matrix, k=1).sum())
    total = home_win + draw + away_win
    home_win, draw, away_win = home_win / total, draw / total, away_win / total

    exact_scores = [
        ScorelineProbability(home, away, float(matrix[home, away]))
        for home in range(_MAX_EXACT_GOALS + 1)
        for away in range(_MAX_EXACT_GOALS + 1)
    ]
    scorelines = sorted(exact_scores, key=lambda item: item.probability, reverse=True)[:3]
    d_conf, d_label = data_confidence(
        ConfidenceInputs(
            data_freshness=context.data_freshness,
            ranking_coverage=context.ranking_coverage,
            history_coverage=context.history_coverage,
            provider_agreement=context.provider_agreement,
        )
    )
    m_conf, m_label = model_confidence(home_win, draw, away_win)
    return MatchPredictionResult(
        home_xg=home_xg,
        away_xg=away_xg,
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        scorelines=scorelines,
        score_matrix=matrix.tolist(),
        confidence=d_conf,
        confidence_label=d_label,
        data_confidence=d_conf,
        data_confidence_label=d_label,
        model_confidence=m_conf,
        model_confidence_label=m_label,
        explanation=explain_prediction(
            context.home_name,
            context.away_name,
            home_win,
            draw,
            away_win,
            strength_delta,
        ),
        model_version=MODEL_VERSION,
    )


def _goal_probabilities(expected_goals: float) -> np.ndarray:
    exact = poisson.pmf(np.arange(_MAX_EXACT_GOALS + 1), expected_goals)
    tail = max(0.0, 1.0 - float(exact.sum()))
    values = np.append(exact, tail)
    return values / values.sum()
