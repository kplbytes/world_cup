from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.standings import MatchResult, rank_group
from app.models import (
    DashboardRevision,
    DataSnapshot,
    Match,
    MatchPrediction,
    QualificationPrediction,
    StandingSnapshot,
    Team,
    TeamRating,
)
from app.prediction.poisson import MODEL_VERSION, MatchContext, predict_match
from app.simulation.qualification import (
    SimulatedMatch,
    SimulationTournament,
    simulate_qualification,
)


def recompute_all(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> DashboardRevision:
    teams = list(session.scalars(select(Team).order_by(Team.group_code, Team.id)))
    matches = list(session.scalars(select(Match).order_by(Match.kickoff, Match.id)))
    if len(teams) != 48 or len(matches) != 72:
        raise ValueError("recompute requires a complete 48-team, 72-match group stage")

    ratings = _latest_ratings(session, teams)
    minimum = min(ratings.values())
    maximum = max(ratings.values())
    spread = maximum - minimum or 1.0
    strengths = {team_id: (rating - minimum) / spread for team_id, rating in ratings.items()}
    groups = {
        group: [team.id for team in teams if team.group_code == group]
        for group in "ABCDEFGHIJKL"
    }
    completed = [
        MatchResult(match.home_team_id, match.away_team_id, match.home_score, match.away_score)
        for match in matches
        if match.status == "final" and match.home_score is not None and match.away_score is not None
    ]

    with session.begin_nested():
        revision = DashboardRevision(
            model_version=MODEL_VERSION,
            simulation_iterations=iterations,
            simulation_seed=seed,
            active=False,
        )
        session.add(revision)
        session.flush()

        for group, group_teams in groups.items():
            team_set = set(group_teams)
            group_results = [
                result
                for result in completed
                if result.home_team_id in team_set and result.away_team_id in team_set
            ]
            table = rank_group(group_teams, group_results)
            for position, row in enumerate(table, start=1):
                session.add(
                    StandingSnapshot(
                        revision_id=revision.id,
                        group_code=group,
                        team_id=row.team_id,
                        position=position,
                        played=row.played,
                        won=row.won,
                        drawn=row.drawn,
                        lost=row.lost,
                        goals_for=row.goals_for,
                        goals_against=row.goals_against,
                        points=row.points,
                        tiebreak_uncertain=row.tiebreak_uncertain,
                    )
                )

        remaining: list[SimulatedMatch] = []
        team_names = {team.id: team.short_name for team in teams}
        freshness, ranking_cov, provider_agree = _compute_data_context(session, teams)
        for match in matches:
            if match.status == "final":
                continue
            prediction = predict_match(
                strengths[match.home_team_id],
                strengths[match.away_team_id],
                MatchContext(
                    data_freshness=freshness,
                    ranking_coverage=ranking_cov,
                    history_coverage=0.65,
                    provider_agreement=provider_agree,
                    home_name=team_names[match.home_team_id],
                    away_name=team_names[match.away_team_id],
                ),
            )
            session.add(
                MatchPrediction(
                    revision_id=revision.id,
                    match_id=match.id,
                    home_xg=prediction.home_xg,
                    away_xg=prediction.away_xg,
                    home_win=prediction.home_win,
                    draw=prediction.draw,
                    away_win=prediction.away_win,
                    scorelines=[
                        {
                            "home_goals": item.home_goals,
                            "away_goals": item.away_goals,
                            "probability": item.probability,
                        }
                        for item in prediction.scorelines
                    ],
                    score_matrix=prediction.score_matrix,
                    confidence=prediction.confidence,
                    confidence_label=prediction.confidence_label,
                    data_confidence=prediction.data_confidence,
                    data_confidence_label=prediction.data_confidence_label,
                    model_confidence=prediction.model_confidence,
                    model_confidence_label=prediction.model_confidence_label,
                    explanation=prediction.explanation,
                    model_inputs={
                        "home_elo": ratings[match.home_team_id],
                        "away_elo": ratings[match.away_team_id],
                    },
                    model_version=prediction.model_version,
                )
            )
            remaining.append(
                SimulatedMatch(
                    id=match.id,
                    group_code=match.group_code,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    score_matrix=prediction.score_matrix,
                )
            )

        qualification = simulate_qualification(
            SimulationTournament(groups=groups, completed=completed, remaining=remaining),
            iterations=iterations,
            seed=seed,
        )
        for item in qualification.teams:
            session.add(
                QualificationPrediction(
                    revision_id=revision.id,
                    team_id=item.team_id,
                    first_probability=item.first,
                    second_probability=item.second,
                    third_probability=item.third,
                    fourth_probability=item.fourth,
                    qualify_probability=item.qualify,
                    standard_error=item.standard_error,
                )
            )

        session.execute(update(DashboardRevision).values(active=False))
        revision.active = True
        session.flush()
    return revision


def _latest_ratings(session: Session, teams: list[Team]) -> dict[str, float]:
    by_team: dict[str, list[TeamRating]] = defaultdict(list)
    for rating in session.scalars(
        select(TeamRating).order_by(TeamRating.team_id, TeamRating.effective_date.desc())
    ):
        by_team[rating.team_id].append(rating)
    missing = [team.id for team in teams if not by_team[team.id]]
    if missing:
        raise ValueError(f"missing team ratings: {missing}")
    return {team.id: by_team[team.id][0].elo for team in teams}


def _compute_data_context(
    session: Session, teams: list[Team]
) -> tuple[float, float, float]:
    """Compute dynamic data context from the current session state.

    Returns (data_freshness, ranking_coverage, provider_agreement).
    """
    now = datetime.now(timezone.utc)

    # Data freshness: based on most recent successful DataSnapshot
    latest_fetch = session.scalar(
        select(DataSnapshot.fetched_at)
        .where(DataSnapshot.status == "available")
        .order_by(DataSnapshot.fetched_at.desc())
        .limit(1)
    )
    if latest_fetch is not None:
        if latest_fetch.tzinfo is None:
            latest_fetch = latest_fetch.replace(tzinfo=timezone.utc)
        age_hours = (now - latest_fetch).total_seconds() / 3600.0
        # Decay: fresh (<1h) = 1.0, stale (>168h / 7 days) = 0.0
        freshness = max(0.0, min(1.0, 1.0 - age_hours / 168.0))
    else:
        freshness = 0.5  # no snapshots yet — assume moderate

    # Ranking coverage: proportion of teams that have ratings
    team_ids = {team.id for team in teams}
    from sqlalchemy import func
    distinct_rated = session.scalar(
        select(func.count(TeamRating.team_id.distinct()))
        .where(TeamRating.team_id.in_(team_ids))
    ) or 0
    ranking_coverage = distinct_rated / max(len(teams), 1)

    # Provider agreement: how many distinct providers have recent ok snapshots
    provider_count = session.scalar(
        select(func.count(DataSnapshot.provider.distinct()))
        .where(DataSnapshot.status == "available")
    ) or 0
    # Single provider = 1.0, multiple = slight boost but capped
    provider_agree = min(1.0, 0.8 + 0.1 * provider_count) if provider_count > 0 else 0.5

    return freshness, ranking_coverage, provider_agree
