from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.standings import MatchResult, rank_group
from app.models import (
    AutoAdjustment,
    DashboardRevision,
    DataSnapshot,
    ManualAdjustment,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    QualificationPrediction,
    StandingSnapshot,
    Team,
    TeamRating,
    TeamProfilePrediction,
)
from app.services.snapshots import write_snapshots
from app.prediction.poisson import MODEL_VERSION, MatchContext, predict_match
from app.prediction.shadow import compute_shadow_predictions
from app.services.localization import localized_team_names
from app.services.manual_adjustments import build_adjustment_context, serialize_adjustment
from app.simulation.qualification import (
    SimulatedMatch,
    SimulationTournament,
    simulate_qualification,
)

import logging
logger = logging.getLogger(__name__)


def recompute_group_stage(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> DashboardRevision:
    """Recompute group stage predictions only (teams from groups A-L, matches with stage="group")."""
    import time
    start = time.monotonic()
    logger.info("recompute_group_stage started")

    teams = list(session.scalars(
        select(Team).where(Team.group_code.in_(list("ABCDEFGHIJKL"))).order_by(Team.group_code, Team.id)
    ))
    matches = list(session.scalars(
        select(Match).where(Match.stage == "group").order_by(Match.kickoff, Match.id)
    ))

    # Validate: all 12 groups have 4 teams and each group has 6 matches
    groups = {
        group: [team.id for team in teams if team.group_code == group]
        for group in "ABCDEFGHIJKL"
    }
    for group_code, group_teams in groups.items():
        if len(group_teams) != 4:
            raise ValueError(f"group {group_code} has {len(group_teams)} teams, expected 4")
        group_match_count = sum(1 for m in matches if m.group_code == group_code)
        if group_match_count != 6:
            raise ValueError(f"group {group_code} has {group_match_count} matches, expected 6")

    ratings = _latest_ratings(session, teams)
    minimum = min(ratings.values())
    maximum = max(ratings.values())
    spread = maximum - minimum or 1.0
    strengths = {team_id: (rating - minimum) / spread for team_id, rating in ratings.items()}
    completed = [
        MatchResult(match.home_team_id, match.away_team_id, match.home_score, match.away_score)
        for match in matches
        if match.status == "final" and match.home_score is not None and match.away_score is not None
    ]

    with session.begin_nested():
        from app.config import settings
        revision = DashboardRevision(
            model_version="elo-poisson-v1-intel-numeric" if settings.enable_numerical_adjustments else MODEL_VERSION,
            simulation_iterations=iterations,
            simulation_seed=seed,
            active=False,
        )
        session.add(revision)
        session.flush()

        compute_standings(session, revision, groups, completed)
        remaining = compute_match_predictions(session, revision, teams, matches, ratings, strengths, groups, completed)
        compute_group_qualification(session, revision, groups, completed, remaining, iterations, seed)

        session.execute(update(DashboardRevision).values(active=False))
        revision.active = True
        session.flush()

        t30_start = time.monotonic()
        write_snapshots(session, revision)
        logger.info("write_snapshots completed in %.2fs", time.monotonic() - t30_start)

    logger.info("recompute_group_stage completed in %.2fs, revision_id=%s", time.monotonic() - start, revision.id)
    return revision


def recompute_knockout_stage(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> DashboardRevision | None:
    """Recompute knockout stage predictions using Elo strengths.

    For placeholder matches (teams TBD), skip prediction.
    Returns None if there are no knockout matches to predict.
    """
    import time
    start = time.monotonic()
    logger.info("recompute_knockout_stage started")

    knockout_matches = list(session.scalars(
        select(Match).where(Match.stage != "group").order_by(Match.kickoff, Match.id)
    ))

    # Filter to matches that have both teams assigned and are not final
    predictable = [
        m for m in knockout_matches
        if m.status != "final" and m.home_team_id is not None and m.away_team_id is not None
    ]

    if not predictable:
        logger.info("recompute_knockout_stage: no predictable knockout matches, skipping")
        return None

    # Get all teams that appear in knockout matches for ratings
    team_ids = set()
    for m in predictable:
        team_ids.add(m.home_team_id)
        team_ids.add(m.away_team_id)
    teams = list(session.scalars(
        select(Team).where(Team.id.in_(team_ids))
    ))

    ratings = _latest_ratings(session, teams)
    minimum = min(ratings.values())
    maximum = max(ratings.values())
    spread = maximum - minimum or 1.0
    strengths = {team_id: (rating - minimum) / spread for team_id, rating in ratings.items()}

    with session.begin_nested():
        from app.config import settings
        revision = DashboardRevision(
            model_version="elo-poisson-v1-intel-numeric" if settings.enable_numerical_adjustments else MODEL_VERSION,
            simulation_iterations=iterations,
            simulation_seed=seed,
            active=False,
        )
        session.add(revision)
        session.flush()

        # Clear auto adjustments only for non-final knockout matches
        non_final_match_ids = {m.id for m in predictable}
        session.execute(
            delete(AutoAdjustment).where(AutoAdjustment.match_id.in_(non_final_match_ids))
        )
        session.flush()

        team_names = localized_team_names(session, teams)
        freshness, ranking_cov, provider_agree = _compute_data_context(session, teams)

        for match in predictable:
            base_ctx = MatchContext(
                data_freshness=freshness,
                ranking_coverage=ranking_cov,
                history_coverage=0.65,
                provider_agreement=provider_agree,
                home_attack_adjustment=0.0,
                home_defense_adjustment=0.0,
                away_attack_adjustment=0.0,
                away_defense_adjustment=0.0,
                home_name=team_names.get(match.home_team_id, match.home_team_id),
                away_name=team_names.get(match.away_team_id, match.away_team_id),
            )
            prediction = predict_match(
                strengths[match.home_team_id],
                strengths[match.away_team_id],
                base_ctx,
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
                    has_auto_adjustments=False,
                    base_home_win=None,
                    base_draw=None,
                    base_away_win=None,
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
                        "knockout_stage": match.stage,
                    },
                    model_version=prediction.model_version,
                )
            )

            # Shadow model predictions for knockout
            shadow_preds = compute_shadow_predictions(
                home_win=prediction.home_win,
                draw=prediction.draw,
                away_win=prediction.away_win,
                home_xg=prediction.home_xg,
                away_xg=prediction.away_xg,
            )
            for sp in shadow_preds:
                shadow_pred = MatchPrediction(
                    revision_id=revision.id,
                    match_id=match.id,
                    home_xg=sp.home_xg,
                    away_xg=sp.away_xg,
                    home_win=sp.home_win,
                    draw=sp.draw,
                    away_win=sp.away_win,
                    has_auto_adjustments=False,
                    base_home_win=sp.home_win,
                    base_draw=sp.draw,
                    base_away_win=sp.away_win,
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
                    explanation=f"Shadow model: {sp.label}",
                    model_inputs={
                        "home_elo": ratings[match.home_team_id],
                        "away_elo": ratings[match.away_team_id],
                        "knockout_stage": match.stage,
                    },
                    model_version=sp.model_version,
                )
                session.add(shadow_pred)

        session.execute(update(DashboardRevision).values(active=False))
        revision.active = True
        session.flush()

        t30_start = time.monotonic()
        write_snapshots(session, revision)
        logger.info("write_snapshots completed in %.2fs", time.monotonic() - t30_start)

    logger.info("recompute_knockout_stage completed in %.2fs, revision_id=%s", time.monotonic() - start, revision.id)
    return revision


def recompute_tournament_projection(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> list:
    """Run the full tournament projection simulation using the qualification module."""
    import time
    start = time.monotonic()
    logger.info("recompute_tournament_projection started")

    from app.tournament.simulation import run_tournament_simulation
    projections = run_tournament_simulation(session, iterations=iterations, seed=seed)

    logger.info("recompute_tournament_projection completed in %.2fs, %d teams", time.monotonic() - start, len(projections))
    return projections


def recompute_all(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> DashboardRevision:
    """Full recompute: group stage, knockout stage, tournament projection, snapshots."""
    import time
    start = time.monotonic()
    logger.info("recompute_all started")

    # Step 1: Group stage
    revision = recompute_group_stage(session, iterations=iterations, seed=seed)

    # Step 2: Knockout stage (may return None if no knockout matches yet)
    recompute_knockout_stage(session, iterations=iterations, seed=seed)

    # Step 3: Tournament projection
    recompute_tournament_projection(session, iterations=iterations, seed=seed)

    logger.info("recompute_all completed in %.2fs, revision_id=%s", time.monotonic() - start, revision.id)
    return revision


def compute_standings(session, revision, groups, completed):
    """Step 1: Compute group standings."""
    import time
    start = time.monotonic()
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
    logger.info("compute_standings completed in %.2fs", time.monotonic() - start)


def compute_match_predictions(session, revision, teams, matches, ratings, strengths, groups, completed):
    """Step 2: Compute match predictions with intelligence adjustments."""
    import time
    start = time.monotonic()

    remaining: list[SimulatedMatch] = []
    team_names = localized_team_names(session, teams)
    raw_manual = list(session.scalars(select(ManualAdjustment)))
    manual_by_match = defaultdict(list)
    for adjustment in raw_manual:
        manual_by_match[adjustment.match_id].append(adjustment)

    from app.intelligence.engine import AdjustmentEngine
    from app.models import MatchIntelligence

    # Clear auto adjustments only for non-final matches
    non_final_match_ids = {match.id for match in matches if match.status != "final"}
    if non_final_match_ids:
        session.execute(
            delete(AutoAdjustment).where(AutoAdjustment.match_id.in_(non_final_match_ids))
        )
        session.flush()

    engine = AdjustmentEngine(session, MODEL_VERSION)

    raw_intel = list(session.scalars(select(MatchIntelligence)))
    intel_by_match = defaultdict(list)
    for intel in raw_intel:
        intel_by_match[intel.match_id].append(intel)

    freshness, ranking_cov, provider_agree = _compute_data_context(session, teams)
    for match in matches:
        if match.status == "final":
            continue

        match_manual = manual_by_match.get(match.id, [])
        manual_ctx = build_adjustment_context(match, match_manual)
        base_ctx = MatchContext(
            data_freshness=freshness,
            ranking_coverage=ranking_cov,
            history_coverage=0.65,
            provider_agreement=provider_agree,
            home_attack_adjustment=manual_ctx.home_attack_adjustment,
            home_defense_adjustment=manual_ctx.home_defense_adjustment,
            away_attack_adjustment=manual_ctx.away_attack_adjustment,
            away_defense_adjustment=manual_ctx.away_defense_adjustment,
            home_name=team_names[match.home_team_id],
            away_name=team_names[match.away_team_id],
        )
        base_prediction = predict_match(
            strengths[match.home_team_id],
            strengths[match.away_team_id],
            base_ctx,
        )

        match_auto = engine.evaluate_match(match, base_prediction, intel_by_match.get(match.id, []))
        has_auto = len(match_auto) > 0

        auto_confidence_penalty = 0.0
        auto_reasons = []

        if has_auto:
            home_attack_auto = sum(a.attack_delta for a in match_auto if a.affected_team_id == match.home_team_id)
            home_defense_auto = sum(a.defense_delta for a in match_auto if a.affected_team_id == match.home_team_id)
            away_attack_auto = sum(a.attack_delta for a in match_auto if a.affected_team_id == match.away_team_id)
            away_defense_auto = sum(a.defense_delta for a in match_auto if a.affected_team_id == match.away_team_id)

            from app.config import settings
            if settings.enable_numerical_adjustments:
                total_abs = abs(home_attack_auto) + abs(home_defense_auto) + abs(away_attack_auto) + abs(away_defense_auto)
                if total_abs > 0.30:
                    scale = 0.30 / total_abs
                    home_attack_auto *= scale
                    home_defense_auto *= scale
                    away_attack_auto *= scale
                    away_defense_auto *= scale

                if total_abs > 0.0:
                    engine.model_version = "elo-poisson-v1-intel-numeric"

            auto_confidence_penalty = sum(a.confidence for a in match_auto)
            auto_reasons = [a.reason for a in match_auto if a.reason]

            adjusted_ctx = MatchContext(
                data_freshness=freshness,
                ranking_coverage=ranking_cov,
                history_coverage=0.65,
                provider_agreement=provider_agree,
                home_attack_adjustment=manual_ctx.home_attack_adjustment + home_attack_auto,
                home_defense_adjustment=manual_ctx.home_defense_adjustment + home_defense_auto,
                away_attack_adjustment=manual_ctx.away_attack_adjustment + away_attack_auto,
                away_defense_adjustment=manual_ctx.away_defense_adjustment + away_defense_auto,
                home_name=team_names[match.home_team_id],
                away_name=team_names[match.away_team_id],
            )
            prediction = predict_match(
                strengths[match.home_team_id],
                strengths[match.away_team_id],
                adjusted_ctx,
            )
            from app.config import settings
            import dataclasses
            if settings.enable_numerical_adjustments and total_abs > 0.0:
                prediction = dataclasses.replace(prediction, model_version="elo-poisson-v1-intel-numeric")
        else:
            prediction = base_prediction

        final_confidence = max(0.0, prediction.confidence + auto_confidence_penalty)
        final_explanation = prediction.explanation
        if auto_reasons:
            final_explanation += f"\n自动修正提示: {'; '.join(auto_reasons)}"

        session.add(
            MatchPrediction(
                revision_id=revision.id,
                match_id=match.id,
                home_xg=prediction.home_xg,
                away_xg=prediction.away_xg,
                home_win=prediction.home_win,
                draw=prediction.draw,
                away_win=prediction.away_win,
                has_auto_adjustments=has_auto,
                base_home_win=base_prediction.home_win if has_auto else None,
                base_draw=base_prediction.draw if has_auto else None,
                base_away_win=base_prediction.away_win if has_auto else None,
                scorelines=[
                    {
                        "home_goals": item.home_goals,
                        "away_goals": item.away_goals,
                        "probability": item.probability,
                    }
                    for item in prediction.scorelines
                ],
                score_matrix=prediction.score_matrix,
                confidence=final_confidence,
                confidence_label=prediction.confidence_label,
                data_confidence=prediction.data_confidence,
                data_confidence_label=prediction.data_confidence_label,
                model_confidence=prediction.model_confidence,
                model_confidence_label=prediction.model_confidence_label,
                explanation=final_explanation,
                model_inputs={
                    "home_elo": ratings[match.home_team_id],
                    "away_elo": ratings[match.away_team_id],
                    "auto_adjustments": [
                        {
                            "affected_team_id": a.affected_team_id,
                            "type": a.adjustment_type,
                            "attack_delta": a.attack_delta,
                            "defense_delta": a.defense_delta,
                            "reason": a.reason,
                        }
                        for a in match_auto
                    ],
                    "manual_context": {
                        "home_attack_adjustment": manual_ctx.home_attack_adjustment,
                        "home_defense_adjustment": manual_ctx.home_defense_adjustment,
                        "away_attack_adjustment": manual_ctx.away_attack_adjustment,
                        "away_defense_adjustment": manual_ctx.away_defense_adjustment,
                    },
                    "manual_adjustments": [
                        serialize_adjustment(adjustment, team_names)
                        for adjustment in match_manual
                    ],
                },
                model_version=prediction.model_version,
            )
        )

        # Shadow model predictions
        shadow_preds = compute_shadow_predictions(
            home_win=prediction.home_win,
            draw=prediction.draw,
            away_win=prediction.away_win,
            home_xg=prediction.home_xg,
            away_xg=prediction.away_xg,
        )
        for sp in shadow_preds:
            shadow_pred = MatchPrediction(
                revision_id=revision.id,
                match_id=match.id,
                home_xg=sp.home_xg,
                away_xg=sp.away_xg,
                home_win=sp.home_win,
                draw=sp.draw,
                away_win=sp.away_win,
                has_auto_adjustments=False,
                base_home_win=sp.home_win,
                base_draw=sp.draw,
                base_away_win=sp.away_win,
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
                explanation=f"Shadow model: {sp.label}",
                model_inputs={
                    "home_elo": ratings[match.home_team_id],
                    "away_elo": ratings[match.away_team_id],
                },
                model_version=sp.model_version,
            )
            session.add(shadow_pred)

        from app.ai.lock_status import compute_match_lock_status
        from app.team_profiles import PROFILE_MODEL_VERSION
        from app.team_profiles.scorer import apply_profile_adjustment
        from app.team_profiles.service import get_team_profile

        home_profile = get_team_profile(session, match.home_team_id, match.kickoff)
        away_profile = get_team_profile(session, match.away_team_id, match.kickoff)
        if home_profile is not None and away_profile is not None:
            profile_result = apply_profile_adjustment(
                {
                    "home_win": prediction.home_win,
                    "draw": prediction.draw,
                    "away_win": prediction.away_win,
                    "home_xg": prediction.home_xg,
                    "away_xg": prediction.away_xg,
                },
                home_profile,
                away_profile,
                ratings[match.home_team_id],
                ratings[match.away_team_id],
            )
            lock = compute_match_lock_status(match)
            session.add(TeamProfilePrediction(
                revision_id=revision.id,
                match_id=match.id,
                model_version=PROFILE_MODEL_VERSION,
                profile_version=home_profile.profile_version,
                profile_as_of=min(home_profile.profile_as_of, away_profile.profile_as_of),
                base_home_win=prediction.home_win,
                base_draw=prediction.draw,
                base_away_win=prediction.away_win,
                home_win=profile_result["probabilities"]["home_win"],
                draw=profile_result["probabilities"]["draw"],
                away_win=profile_result["probabilities"]["away_win"],
                home_xg=profile_result["xg"]["home"],
                away_xg=profile_result["xg"]["away"],
                probability_deltas_json=profile_result["probability_deltas"],
                xg_deltas_json=profile_result["xg_deltas"],
                risk_flags_json=profile_result["risk_flags"],
                triggered_traits_json=list(dict.fromkeys(home_profile.traits_json + away_profile.traits_json)),
                explanation=profile_result["explanation"],
                is_pre_match_locked=lock.is_pre_match_locked,
                is_fallback_locked=lock.is_fallback_locked,
                real_time_only=lock.real_time_only,
                locked_at=lock.locked_at,
            ))
        remaining.append(
            SimulatedMatch(
                id=match.id,
                group_code=match.group_code,
                home_team_id=match.home_team_id,
                away_team_id=match.away_team_id,
                score_matrix=prediction.score_matrix,
            )
        )

    logger.info("compute_match_predictions completed in %.2fs, %d matches", time.monotonic() - start, len(remaining))
    return remaining


def compute_group_qualification(session, revision, groups, completed, remaining, iterations, seed):
    """Step 3: Run Monte Carlo qualification simulation."""
    import time
    start = time.monotonic()

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

    logger.info("compute_group_qualification completed in %.2fs, %d teams", time.monotonic() - start, len(qualification.teams))


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
