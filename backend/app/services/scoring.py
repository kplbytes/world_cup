"""Post-match model scoring engine.

Compares prediction snapshots against actual match results to produce
Brier scores, log loss, hit rates, and expected goals error.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, ModelScore, PredictionSnapshot

_CLIP = 1e-6


@dataclass(frozen=True)
class MatchScoreDetail:
    match_id: str
    home_team: str
    away_team: str
    predicted: dict[str, float]
    actual: dict[str, int]
    brier: float
    outcome_correct: bool
    top_score_correct: bool
    xg_error: float


@dataclass(frozen=True)
class ModelScoreReport:
    matches_scored: int
    brier_score: float
    log_loss: float
    outcome_hit_rate: float
    top_score_hit_rate: float
    xg_mae: float
    per_match: list[MatchScoreDetail] = field(default_factory=list)


def score_predictions(
    snapshots: list[tuple[PredictionSnapshot, Match]],
    team_names: dict[str, str] | None = None,
) -> ModelScoreReport:
    """Score a list of (snapshot, finalised-match) pairs.

    Pure function — does not touch the database.
    """
    if not snapshots:
        return ModelScoreReport(
            matches_scored=0,
            brier_score=0.0,
            log_loss=0.0,
            outcome_hit_rate=0.0,
            top_score_hit_rate=0.0,
            xg_mae=0.0,
        )

    names = team_names or {}
    brier_sum = 0.0
    log_loss_sum = 0.0
    outcome_hits = 0
    top_score_hits = 0
    xg_error_sum = 0.0
    details: list[MatchScoreDetail] = []

    for snap, match in snapshots:
        actual_home = match.home_score or 0
        actual_away = match.away_score or 0

        # Outcome indicators
        if actual_home > actual_away:
            o_home, o_draw, o_away = 1.0, 0.0, 0.0
        elif actual_home == actual_away:
            o_home, o_draw, o_away = 0.0, 1.0, 0.0
        else:
            o_home, o_draw, o_away = 0.0, 0.0, 1.0

        p_home = snap.home_win
        p_draw = snap.draw
        p_away = snap.away_win

        # Brier score for this match: sum of squared errors for each outcome
        brier = (p_home - o_home) ** 2 + (p_draw - o_draw) ** 2 + (p_away - o_away) ** 2
        brier_sum += brier

        # Log loss (clip probabilities to avoid log(0))
        cp_home = max(_CLIP, min(1 - _CLIP, p_home))
        cp_draw = max(_CLIP, min(1 - _CLIP, p_draw))
        cp_away = max(_CLIP, min(1 - _CLIP, p_away))
        ll = -(o_home * math.log(cp_home) + o_draw * math.log(cp_draw) + o_away * math.log(cp_away))
        log_loss_sum += ll

        # Outcome hit: model's most probable outcome matches actual
        predicted_outcome = max(
            [("home", p_home), ("draw", p_draw), ("away", p_away)],
            key=lambda x: x[1],
        )[0]
        actual_outcome = "home" if o_home == 1.0 else "draw" if o_draw == 1.0 else "away"
        outcome_correct = predicted_outcome == actual_outcome
        if outcome_correct:
            outcome_hits += 1

        # Top score hit: actual scoreline is in the predicted top 3
        actual_scoreline = (actual_home, actual_away)
        top_scores = [(s.get("home_goals", s[0] if isinstance(s, (list, tuple)) else 0),
                       s.get("away_goals", s[1] if isinstance(s, (list, tuple)) else 0))
                      for s in snap.scorelines]
        top_score_correct = actual_scoreline in top_scores
        if top_score_correct:
            top_score_hits += 1

        # xG error
        xg_error = (abs(snap.home_xg - actual_home) + abs(snap.away_xg - actual_away)) / 2.0
        xg_error_sum += xg_error

        details.append(
            MatchScoreDetail(
                match_id=match.id,
                home_team=names.get(match.home_team_id, match.home_team_id),
                away_team=names.get(match.away_team_id, match.away_team_id),
                predicted={"home_win": p_home, "draw": p_draw, "away_win": p_away},
                actual={"home_score": actual_home, "away_score": actual_away},
                brier=brier,
                outcome_correct=outcome_correct,
                top_score_correct=top_score_correct,
                xg_error=xg_error,
            )
        )

    n = len(snapshots)
    return ModelScoreReport(
        matches_scored=n,
        brier_score=brier_sum / n,
        log_loss=log_loss_sum / n,
        outcome_hit_rate=outcome_hits / n,
        top_score_hit_rate=top_score_hits / n,
        xg_mae=xg_error_sum / n,
        per_match=details,
    )


def score_model(session: Session) -> ModelScoreReport:
    """Score all finalised matches that have prediction snapshots.

    Reads from the database and returns a report.
    """
    from app.models import Team

    # Build team name lookup
    team_names = {
        row.id: row.short_name
        for row in session.scalars(select(Team))
    }

    # Find all snapshots whose match is final
    rows = session.execute(
        select(PredictionSnapshot, Match)
        .join(Match, PredictionSnapshot.match_id == Match.id)
        .where(Match.status == "final")
    ).all()

    pairs = [(snap, match) for snap, match in rows]
    return score_predictions(pairs, team_names)


def save_model_score(session: Session, report: ModelScoreReport, revision_id: int) -> ModelScore | None:
    """Persist a ModelScore row from a report. Returns the saved row or None if empty."""
    if report.matches_scored == 0:
        return None
    row = ModelScore(
        revision_id=revision_id,
        matches_scored=report.matches_scored,
        brier_score=report.brier_score,
        log_loss=report.log_loss,
        outcome_hit_rate=report.outcome_hit_rate,
        top_score_hit_rate=report.top_score_hit_rate,
        xg_mae=report.xg_mae,
        per_match=[
            {
                "match_id": d.match_id,
                "home_team": d.home_team,
                "away_team": d.away_team,
                "predicted": d.predicted,
                "actual": d.actual,
                "brier": d.brier,
                "outcome_correct": d.outcome_correct,
                "top_score_correct": d.top_score_correct,
                "xg_error": d.xg_error,
            }
            for d in report.per_match
        ],
    )
    session.add(row)
    session.flush()
    return row


def snapshot_prediction(session: Session, match_id: str) -> PredictionSnapshot | None:
    """Copy the current active MatchPrediction for a match into PredictionSnapshot.

    Called when a match transitions to 'final'. Returns the snapshot or None if
    no active prediction exists.
    """
    from app.models import DashboardRevision, MatchPrediction

    active_rev = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if active_rev is None:
        return None

    pred = session.scalar(
        select(MatchPrediction).where(
            MatchPrediction.revision_id == active_rev.id,
            MatchPrediction.match_id == match_id,
        )
    )
    if pred is None:
        return None

    # Avoid duplicates: check if snapshot already exists
    existing = session.get(PredictionSnapshot, match_id)
    if existing is not None:
        return existing

    snap = PredictionSnapshot(
        match_id=match_id,
        revision_id=active_rev.id,
        home_win=pred.home_win,
        draw=pred.draw,
        away_win=pred.away_win,
        home_xg=pred.home_xg,
        away_xg=pred.away_xg,
        scorelines=pred.scorelines,
        score_matrix=pred.score_matrix,
        confidence=pred.confidence,
        confidence_label=pred.confidence_label,
        model_inputs=pred.model_inputs,
        model_version=pred.model_version,
    )
    session.add(snap)
    session.flush()
    return snap
