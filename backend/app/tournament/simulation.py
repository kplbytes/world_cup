"""Tournament Monte Carlo simulation for full World Cup cycle."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, QualificationPrediction, Team, TeamRating
from app.tournament.qualification import TeamProjection, compute_projections


def run_tournament_simulation(
    session: Session,
    iterations: int = 10_000,
    seed: int = 20260613,
) -> list[TeamProjection]:
    """Run a full tournament simulation including group qualification and knockout.

    Uses existing QualificationPrediction data for group stage probabilities,
    then simulates knockout rounds.
    """
    # Get qualification probabilities from existing simulation
    qual_preds = list(session.scalars(select(QualificationPrediction)))
    if not qual_preds:
        return []

    # Build group placement probabilities: team_id -> {"1st": p, "2nd": p, "3rd": p, "4th": p}
    group_placement_probs = {}
    for pred in qual_preds:
        group_placement_probs[pred.team_id] = {
            "1st": pred.first_probability,
            "2nd": pred.second_probability,
            "3rd": pred.third_probability,
            "4th": pred.fourth_probability,
        }

    # Get Elo ratings and team-group mapping
    teams = list(session.scalars(select(Team)))
    ratings = _get_latest_elos(session, teams)
    team_group_map = {t.id: t.group_code for t in teams}

    # Run full tournament projection
    projections = compute_projections(
        group_placement_probs, ratings, team_group_map, iterations, seed,
    )

    return projections


def get_team_path(
    session: Session,
    team_id: str,
) -> dict[str, Any]:
    """Get a team's potential tournament path."""
    projections = run_tournament_simulation(session, iterations=5000)

    team_proj = next((p for p in projections if p.team_id == team_id), None)
    if not team_proj:
        return {"team_id": team_id, "found": False}

    team = session.get(Team, team_id)
    team_name = team.short_name if team else team_id

    # Get the team's group matches
    matches = list(session.scalars(
        select(Match)
        .where(Match.stage == "group")
        .where((Match.home_team_id == team_id) | (Match.away_team_id == team_id))
        .order_by(Match.kickoff)
    ))

    match_list = []
    for m in matches:
        home_team = session.get(Team, m.home_team_id)
        away_team = session.get(Team, m.away_team_id)
        match_list.append({
            "match_id": m.id,
            "home_team": home_team.short_name if home_team else "?",
            "away_team": away_team.short_name if away_team else "?",
            "kickoff": m.kickoff.isoformat() if m.kickoff else "",
            "status": m.status,
            "home_score": m.home_score,
            "away_score": m.away_score,
        })

    return {
        "team_id": team_id,
        "team_name": team_name,
        "found": True,
        "group_matches": match_list,
        "projections": {
            "group_qualify": team_proj.group_qualify,
            "round_of_32": team_proj.round_of_32,
            "round_of_16": team_proj.round_of_16,
            "quarter_final": team_proj.quarter_final,
            "semi_final": team_proj.semi_final,
            "final": team_proj.final,
            "champion": team_proj.champion,
        },
    }


def _get_latest_elos(session: Session, teams: list[Team]) -> dict[str, float]:
    """Get latest Elo ratings for all teams."""
    from collections import defaultdict

    by_team: dict[str, list[TeamRating]] = defaultdict(list)
    for rating in session.scalars(
        select(TeamRating).order_by(TeamRating.team_id, TeamRating.effective_date.desc())
    ):
        by_team[rating.team_id].append(rating)

    result = {}
    for team in teams:
        if by_team[team.id]:
            result[team.id] = by_team[team.id][0].elo
        else:
            result[team.id] = 1500.0  # default
    return result
