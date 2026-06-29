from collections import defaultdict
from datetime import datetime, timedelta, timezone
import threading

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.standings import MatchResult, rank_group
from app.models import (
    AutoAdjustment,
    AIPrediction,
    DashboardRevision,
    DataSnapshot,
    ManualAdjustment,
    MarketSnapshot,
    Match,
    MatchPrediction,
    PredictionSnapshot,
    QualificationPrediction,
    StandingSnapshot,
    Team,
    TeamRating,
)
from app.services.snapshots import write_snapshots
from app.prediction.poisson import MODEL_VERSION, MatchContext, predict_match
from app.prediction.shadow import compute_shadow_predictions
from app.services.localization import localized_team_names
from app.services.manual_adjustments import build_adjustment_context, serialize_adjustment
from app.services.calibration_feedback import compute_and_persist_feedback, get_feedback_overrides
from app.prediction.shadow_promotion import evaluate_shadow_promotion, get_promoted_params
from app.tournament.knockout import sync_knockout_state
from app.simulation.qualification import (
    SimulatedMatch,
    SimulationTournament,
    simulate_qualification,
)

import logging
logger = logging.getLogger(__name__)


# Process-level mutex: prevents concurrent recompute_all executions from
# exhausting the SQLAlchemy connection pool (SQLite single-writer limitation).
# When a recompute is already running, subsequent callers skip and return the
# current active revision instead of queueing on the pool.
_recompute_lock = threading.Lock()


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
    elo_map = {tid: r.elo for tid, r in ratings.items()}
    minimum = min(elo_map.values())
    maximum = max(elo_map.values())
    spread = maximum - minimum or 1.0
    strengths = {team_id: (elo - minimum) / spread for team_id, elo in elo_map.items()}
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
    revision: DashboardRevision | None = None,
    activate_revision: bool = True,
) -> DashboardRevision | None:
    """Recompute knockout stage predictions using Elo strengths.

    For placeholder matches (teams TBD), skip prediction.
    Returns None if there are no knockout matches to predict.
    """
    import time
    start = time.monotonic()
    logger.info("recompute_knockout_stage started")
    sync_knockout_state(session)

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
    elo_map = {tid: r.elo for tid, r in ratings.items()}
    # Use full tournament (all 48 teams) Elo min/max for normalization so
    # strength_delta is consistent between group and knockout stages. If we
    # only used knockout teams (typically 16), the narrower Elo range would
    # inflate strength_delta and over-amplify xG differences.
    all_teams = list(session.scalars(select(Team)))
    all_ratings = _latest_ratings(session, all_teams)
    all_elo = [r.elo for r in all_ratings.values()]
    minimum = min(all_elo) if all_elo else min(elo_map.values())
    maximum = max(all_elo) if all_elo else max(elo_map.values())
    spread = maximum - minimum or 1.0
    strengths = {team_id: (elo - minimum) / spread for team_id, elo in elo_map.items()}

    with session.begin_nested():
        from app.config import settings
        if revision is None:
            revision = DashboardRevision(
                model_version="elo-poisson-v1-intel-numeric" if settings.enable_numerical_adjustments else MODEL_VERSION,
                simulation_iterations=iterations,
                simulation_seed=seed,
                active=False,
            )
            session.add(revision)
            session.flush()

        knockout_match_ids = {m.id for m in knockout_matches}
        if knockout_match_ids:
            session.execute(
                delete(MatchPrediction).where(
                    MatchPrediction.revision_id == revision.id,
                    MatchPrediction.match_id.in_(knockout_match_ids),
                )
            )
            session.flush()

        # Clear auto adjustments only for non-final knockout matches
        non_final_match_ids = {m.id for m in predictable}
        session.execute(
            delete(AutoAdjustment).where(AutoAdjustment.match_id.in_(non_final_match_ids))
        )
        session.flush()

        team_names = localized_team_names(session, teams)
        freshness, ranking_cov, provider_agree = _compute_data_context(session, teams)

        # Load market snapshots for knockout matches
        raw_market_ko = list(session.scalars(
            select(MarketSnapshot).where(MarketSnapshot.match_id.in_({m.id for m in predictable}))
        ))
        market_by_match_ko: dict[str, dict[str, float]] = {}
        for snap in raw_market_ko:
            existing = market_by_match_ko.get(snap.match_id)
            if existing is None or snap.fetched_at > market_by_match_ko[snap.match_id].get("_fetched_at", datetime.min.replace(tzinfo=timezone.utc)):
                market_by_match_ko[snap.match_id] = {
                    "home_win": snap.home_probability,
                    "draw": snap.draw_probability,
                    "away_win": snap.away_probability,
                    "_fetched_at": snap.fetched_at,
                }
        for mid in market_by_match_ko:
            market_by_match_ko[mid].pop("_fetched_at", None)

        # Research-enhanced config for knockout matches.
        # Knockout matches have lower base goals (~2.4 vs ~2.9 in group stage),
        # benefit from Poisson over-dispersion (more low-scoring games), and
        # need favorite_dampening to avoid overconfidence in tight knockout games.
        _ko_feedback = {**get_feedback_overrides(), **get_promoted_params()}
        _ko_config = type("Config", (), {
            "market_blend_weight": 0.20,
            "smart_market_blend": True,
            "dynamic_draw_boost": True,
            "profile_weight": 0.15,
            "favorite_dampening": _ko_feedback.get("favorite_dampening", 0.05),
            "poisson_dispersion": 1.10,
            "base_goal_mean_home": 1.40,
            "base_goal_mean_away": 1.20,
        })()

        from app.team_profiles.service import get_team_profile
        from app.prediction.profile_adapter import compute_profile_adjustments

        _profile_enabled = getattr(_ko_config, 'profile_weight', 0.0) > 0
        _profile_map: dict[str, object] = {}
        if _profile_enabled:
            for team in teams:
                profile = get_team_profile(session, team.id)
                if profile is not None:
                    _profile_map[team.id] = profile

        for match in predictable:
            home_str = strengths[match.home_team_id]
            away_str = strengths[match.away_team_id]
            elo_closeness = 1.0 - abs(home_str - away_str)
            is_group = False  # knockout stage
            match_market = market_by_match_ko.get(match.id)
            # Third-place play-offs are notoriously lower-intensity: teams
            # are typically mentally fatigued after a semi-final loss and
            # rotation/rest is common. Apply a fatigue/xg-attenuation tweak
            # by overriding a couple of config knobs for the third-place
            # match specifically (rather than a new config class).
            is_third_place = match.stage == "third_place"
            if is_third_place:
                match_config = type("Config", (), {
                    "market_blend_weight": 0.15,        # less market trust (rotations)
                    "smart_market_blend": True,
                    "dynamic_draw_boost": True,
                    "profile_weight": 0.10,            # less profile weight
                    "favorite_dampening": 0.05,        # small dampening (upsets common)
                })()
            else:
                match_config = _ko_config

            # FIFA rank delta
            fifa_rank_delta = 0.0
            home_rating = ratings.get(match.home_team_id)
            away_rating = ratings.get(match.away_team_id)
            if home_rating and away_rating:
                home_fifa = getattr(home_rating, 'fifa_rank', None)
                away_fifa = getattr(away_rating, 'fifa_rank', None)
                if home_fifa and away_fifa:
                    fifa_rank_delta = home_fifa - away_fifa

            prof = {"profile_available": False}  # type: dict[str, Any]
            if _profile_enabled:
                home_p = _profile_map.get(match.home_team_id)
                away_p = _profile_map.get(match.away_team_id)
                prof = compute_profile_adjustments(home_p, away_p)

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
                market_probs=match_market,
                fifa_rank_delta=fifa_rank_delta,
                is_group_stage=is_group,
                elo_closeness=elo_closeness,
                profile_home_attack=prof.get("profile_home_attack", 0.0),
                profile_home_defense=prof.get("profile_home_defense", 0.0),
                profile_away_attack=prof.get("profile_away_attack", 0.0),
                profile_away_defense=prof.get("profile_away_defense", 0.0),
                profile_home_form=prof.get("profile_home_form", 0.0),
                profile_away_form=prof.get("profile_away_form", 0.0),
                profile_draw_adjustment=prof.get("profile_draw_adjustment", 0.0),
                profile_available=prof.get("profile_available", False),
                profile_risk_flags=prof.get("profile_risk_flags"),
                is_knockout=True,
            )
            prediction = predict_match(
                strengths[match.home_team_id],
                strengths[match.away_team_id],
                base_ctx,
                config=match_config,
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
                        "home_elo": ratings[match.home_team_id].elo,
                        "away_elo": ratings[match.away_team_id].elo,
                        "knockout_stage": match.stage,
                        "fifa_rank_delta": fifa_rank_delta,
                        "elo_closeness": elo_closeness,
                        "is_group_stage": is_group,
                        "is_knockout": True,
                        # Two-stage advance probabilities (extra-time + penalties).
                        # Stored inside model_inputs so the schema stays unchanged
                        # but downstream scoring / dashboards can still read them.
                        "home_advance": prediction.home_advance,
                        "away_advance": prediction.away_advance,
                        "fifa_rank_adjustment": -fifa_rank_delta / 40.0 * 0.2 * 0.15 if fifa_rank_delta else 0.0,
                        "profile_adjustments": {
                            "home_attack": prof.get("profile_home_attack", 0.0),
                            "home_defense": prof.get("profile_home_defense", 0.0),
                            "away_attack": prof.get("profile_away_attack", 0.0),
                            "away_defense": prof.get("profile_away_defense", 0.0),
                            "home_form": prof.get("profile_home_form", 0.0),
                            "away_form": prof.get("profile_away_form", 0.0),
                            "draw": prof.get("profile_draw_adjustment", 0.0),
                        },
                        "profile_available": prof.get("profile_available", False),
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
                market_probs=match_market,
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
                        "home_elo": ratings[match.home_team_id].elo,
                        "away_elo": ratings[match.away_team_id].elo,
                        "knockout_stage": match.stage,
                        "fifa_rank_delta": fifa_rank_delta,
                        "elo_closeness": elo_closeness,
                        "is_group_stage": is_group,
                    },
                    model_version=sp.model_version,
                )
                session.add(shadow_pred)

        if activate_revision:
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


def _prune_old_revisions(session: Session, keep: int = 5) -> None:
    """Delete non-scoring data from old revisions to prevent database bloat.

    P2-G improvements over the original:
      - Prune in dependency order: child tables first, then parent
        dashboard_revisions, so the EXISTS-guarded deletion can actually
        succeed.
      - Keep model_scores for historical analysis (twice the keep window)
        but still prune them eventually to avoid unbounded growth.
      - Keep match_predictions and prediction_snapshots indefinitely because
        they ARE scoring history — pruning them would break trend analysis
        and the test_prune_old_revisions_preserves_scoring_history contract.
        Old dashboard_revisions whose scoring history is intact are kept by
        design (the EXISTS checks in Step 4 ensure this).
    """
    from sqlalchemy import func, text

    max_id = session.scalar(select(func.max(DashboardRevision.id)))
    if max_id is None:
        return
    cutoff = max_id - keep
    if cutoff <= 0:
        return
    # Score history is more valuable than prediction history — keep 2x
    # window for model_scores so trend charts remain meaningful.
    score_cutoff = max_id - (keep * 2)

    # Step 1: prune child tables that are NOT scoring history.
    # match_predictions / prediction_snapshots / model_scores are scoring
    # history and must be preserved so old revisions stay queryable for
    # trend analysis. Only non-scoring auxiliary tables are pruned here.
    # NOTE: only tables that have a revision_id column can be pruned here.
    # auto_adjustments / ensemble_predictions / ai_predictions are pruned by
    # created_at in Step 5 below because they don't have revision_id.
    child_tables = [
        "qualification_predictions",
        "standings_snapshots",
        "team_profile_predictions",
    ]
    for table in child_tables:
        result = session.execute(
            text(f"DELETE FROM {table} WHERE revision_id <= :cutoff"),
            {"cutoff": cutoff},
        )
        if result.rowcount:
            logger.info("pruned %d rows from %s (revision <= %d)", result.rowcount, table, cutoff)

    # Step 2: prune old model_scores (with a larger keep window so trend
    # charts keep historical context).
    if score_cutoff > 0:
        score_result = session.execute(
            text("DELETE FROM model_scores WHERE revision_id <= :cutoff"),
            {"cutoff": score_cutoff},
        )
        if score_result.rowcount:
            logger.info("pruned %d rows from model_scores (revision <= %d)", score_result.rowcount, score_cutoff)

    # Step 3: delete old inactive dashboard_revisions whose scoring history
    # has been pruned. After Steps 1-2, the EXISTS checks should succeed for
    # revisions older than the cutoff whose scoring history was pruned.
    # Revisions with surviving match_predictions / prediction_snapshots /
    # model_scores are kept by design (those are scoring history).
    session.execute(
        text(
            """
            DELETE FROM dashboard_revisions
            WHERE id <= :cutoff
              AND active = 0
              AND NOT EXISTS (
                  SELECT 1 FROM prediction_snapshots
                  WHERE prediction_snapshots.revision_id = dashboard_revisions.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM match_predictions
                  WHERE match_predictions.revision_id = dashboard_revisions.id
              )
              AND NOT EXISTS (
                  SELECT 1 FROM model_scores
                  WHERE model_scores.revision_id = dashboard_revisions.id
              )
            """
        ),
        {"cutoff": cutoff},
    )

    # Step 4: prune truly time-bound auxiliary tables by created_at.
    # Only prune tables whose data loses value rapidly (news, intelligence
    # signals) — keep ai_predictions, market_snapshots, ensemble_predictions
    # indefinitely because they're used for historical evaluation / scoring.
    from app.config import settings as _settings
    retention_days = getattr(_settings, "aux_data_retention_days", 90)
    if retention_days > 0:
        time_bound_aux_tables = [
            # Match intelligence (news, injury reports) is only useful
            # for the immediate pre-match window. Prune aggressively.
            "match_intelligence",
        ]
        for table in time_bound_aux_tables:
            try:
                result = session.execute(
                    text(
                        f"DELETE FROM {table} "
                        "WHERE fetched_at < datetime('now', :days) "
                        "AND fetched_at IS NOT NULL"
                    ),
                    {"days": f"-{retention_days} days"},
                )
                if result.rowcount:
                    logger.info(
                        "pruned %d rows from %s (older than %d days)",
                        result.rowcount, table, retention_days,
                    )
            except Exception as exc:
                logger.debug("skip aux prune for %s: %s", table, exc)
    session.flush()


def recompute_all(
    session: Session,
    iterations: int = 50_000,
    seed: int = 20260613,
) -> DashboardRevision:
    """Full recompute: group stage, knockout stage, tournament projection, snapshots.

    Process-level mutex prevents concurrent executions. If another recompute is
    already running, this call skips and returns the current active revision —
    the data remains consistent, just not freshly recomputed by this caller.
    """
    if not _recompute_lock.acquire(blocking=False):
        logger.warning("recompute_all skipped: another recompute is already running")
        return session.scalar(
            select(DashboardRevision)
            .where(DashboardRevision.active.is_(True))
            .order_by(DashboardRevision.id.desc())
            .limit(1)
        )

    import time
    start = time.monotonic()
    logger.info("recompute_all started")
    try:
        sync_knockout_state(session)

        # Step 1: Group stage
        revision = recompute_group_stage(session, iterations=iterations, seed=seed)

        # Step 2: Knockout stage predictions are written into the same active revision
        recompute_knockout_stage(
            session,
            iterations=iterations,
            seed=seed,
            revision=revision,
            activate_revision=False,
        )

        # Step 3: Tournament projection
        recompute_tournament_projection(session, iterations=iterations, seed=seed)

        # Step 4: Calibration feedback — analyze observed bias and persist
        # parameter overrides for the next recompute cycle. Safe no-op when
        # there are too few scored matches (< 15).
        try:
            feedback = compute_and_persist_feedback(session)
            if feedback:
                logger.info("Calibration feedback applied: %s", feedback)
        except Exception as exc:
            logger.warning("Calibration feedback failed (non-fatal): %s", exc)

        # Step 5: Shadow model promotion — evaluate shadow models vs baseline
        # and promote any that consistently outperform. Persists params to disk.
        try:
            promotion = evaluate_shadow_promotion(session)
            if promotion.get("promoted"):
                logger.info("Shadow promotion: %s", promotion)
        except Exception as exc:
            logger.warning("Shadow promotion evaluation failed (non-fatal): %s", exc)

        logger.info("recompute_all completed in %.2fs, revision_id=%s", time.monotonic() - start, revision.id)

        # Invalidate dashboard + decision caches so next request sees fresh data
        try:
            from app.services.dashboard import invalidate_dashboard_caches
            invalidate_dashboard_caches()
        except Exception as exc:
            logger.warning("Failed to invalidate dashboard caches (non-fatal): %s", exc)

        # Also invalidate the tournament projections cache (300s TTL) so
        # users see updated champion probabilities immediately after recompute.
        try:
            import app.api.routes.tournament_routes as _tour_mod
            _tour_mod._projections_cache = None
            _tour_mod._projections_cache_ts = 0.0
        except Exception:
            pass

        # Clean up old revisions to prevent unbounded database growth.
        # Keep at most 5 revisions total (including the one just created).
        _prune_old_revisions(session, keep=5)

        return revision
    finally:
        _recompute_lock.release()


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

    # Load market snapshots for blending
    raw_market = list(session.scalars(select(MarketSnapshot)))
    market_by_match: dict[str, dict[str, float]] = {}
    for snap in raw_market:
        existing = market_by_match.get(snap.match_id)
        if existing is None or snap.fetched_at > market_by_match[snap.match_id].get("_fetched_at", datetime.min.replace(tzinfo=timezone.utc)):
            market_by_match[snap.match_id] = {
                "home_win": snap.home_probability,
                "draw": snap.draw_probability,
                "away_win": snap.away_probability,
                "_fetched_at": snap.fetched_at,
            }
    # Clean up internal key
    for mid in market_by_match:
        market_by_match[mid].pop("_fetched_at", None)

    # Config for v3-profile-enhanced predictions
    # Profile features are loaded from TeamProfile data and blended at 15% weight
    # FIFA rank weight reduced to 10% since profile attack/defense now carries more signal
    # Merge calibration feedback + shadow promotion overrides
    _feedback = {**get_feedback_overrides(), **get_promoted_params()}
    _market_blend_config = type("Config", (), {
        "market_blend_weight": 0.20,
        "smart_market_blend": True,
        "dynamic_draw_boost": True,
        "profile_weight": 0.15,
        "profile_adjust_attack_defense": True,
        "profile_adjust_form": True,
        "fifa_rank_weight": 0.10,
        "base_goal_mean_home": 1.55,
        "base_goal_mean_away": 1.35,
        "strength_coeff_home": 1.20,
        "strength_coeff_away": 1.00,
        "poisson_dispersion": 1.10,
        "max_xg": 4.50,
        "favorite_dampening": _feedback.get("favorite_dampening", 0.05),
    })()

    # Load team profiles for profile-enhanced predictions
    from app.team_profiles.service import get_team_profile
    from app.prediction.profile_adapter import compute_profile_adjustments

    _profile_enabled = getattr(_market_blend_config, 'profile_weight', 0.0) > 0
    _profile_map: dict[str, object] = {}
    if _profile_enabled:
        for team in teams:
            profile = get_team_profile(session, team.id)
            if profile is not None:
                _profile_map[team.id] = profile

    freshness, ranking_cov, provider_agree = _compute_data_context(session, teams)
    for match in matches:
        if match.status == "final":
            continue

        match_manual = manual_by_match.get(match.id, [])
        manual_ctx = build_adjustment_context(match, match_manual)
        match_market = market_by_match.get(match.id)
        match_config = _market_blend_config

        # Research-enhanced: compute elo_closeness and group stage flag
        home_str = strengths[match.home_team_id]
        away_str = strengths[match.away_team_id]
        elo_closeness = 1.0 - abs(home_str - away_str)
        is_group = match.stage == "group"

        # FIFA rank delta from team_ratings
        fifa_rank_delta = 0.0
        home_rating = ratings.get(match.home_team_id)
        away_rating = ratings.get(match.away_team_id)
        if home_rating and away_rating:
            home_fifa = getattr(home_rating, 'fifa_rank', None)
            away_fifa = getattr(away_rating, 'fifa_rank', None)
            if home_fifa and away_fifa:
                fifa_rank_delta = home_fifa - away_fifa  # negative = home ranked higher

        # Compute profile-derived adjustments
        prof = {"profile_available": False}  # type: dict[str, Any]
        if _profile_enabled:
            home_p = _profile_map.get(match.home_team_id)
            away_p = _profile_map.get(match.away_team_id)
            prof = compute_profile_adjustments(home_p, away_p)

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
            market_probs=match_market,
            fifa_rank_delta=fifa_rank_delta,
            is_group_stage=is_group,
            elo_closeness=elo_closeness,
            profile_home_attack=prof.get("profile_home_attack", 0.0),
            profile_home_defense=prof.get("profile_home_defense", 0.0),
            profile_away_attack=prof.get("profile_away_attack", 0.0),
            profile_away_defense=prof.get("profile_away_defense", 0.0),
            profile_home_form=prof.get("profile_home_form", 0.0),
            profile_away_form=prof.get("profile_away_form", 0.0),
            profile_draw_adjustment=prof.get("profile_draw_adjustment", 0.0),
            profile_available=prof.get("profile_available", False),
            profile_risk_flags=prof.get("profile_risk_flags"),
        )
        base_prediction = predict_match(
            strengths[match.home_team_id],
            strengths[match.away_team_id],
            base_ctx,
            config=match_config,
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
            total_abs = abs(home_attack_auto) + abs(home_defense_auto) + abs(away_attack_auto) + abs(away_defense_auto)
            if settings.enable_numerical_adjustments:
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
                home_attack_adjustment=home_attack_auto,
                home_defense_adjustment=home_defense_auto,
                away_attack_adjustment=away_attack_auto,
                away_defense_adjustment=away_defense_auto,
                home_name=team_names[match.home_team_id],
                away_name=team_names[match.away_team_id],
                market_probs=match_market,
                fifa_rank_delta=fifa_rank_delta,
                is_group_stage=is_group,
                elo_closeness=elo_closeness,
                profile_home_attack=prof.get("profile_home_attack", 0.0),
                profile_home_defense=prof.get("profile_home_defense", 0.0),
                profile_away_attack=prof.get("profile_away_attack", 0.0),
                profile_away_defense=prof.get("profile_away_defense", 0.0),
                profile_home_form=prof.get("profile_home_form", 0.0),
                profile_away_form=prof.get("profile_away_form", 0.0),
                profile_draw_adjustment=prof.get("profile_draw_adjustment", 0.0),
                profile_available=prof.get("profile_available", False),
                profile_risk_flags=prof.get("profile_risk_flags"),
            )
            prediction = predict_match(
                strengths[match.home_team_id],
                strengths[match.away_team_id],
                adjusted_ctx,
                config=match_config,
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
                    "home_elo": ratings[match.home_team_id].elo,
                    "away_elo": ratings[match.away_team_id].elo,
                    "fifa_rank_delta": fifa_rank_delta,
                    "elo_closeness": elo_closeness,
                    "is_group_stage": is_group,
                    "fifa_rank_adjustment": -fifa_rank_delta / 40.0 * 0.2 * 0.15 if fifa_rank_delta else 0.0,
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
                    "profile_adjustments": {
                        "home_attack": prof.get("profile_home_attack", 0.0),
                        "home_defense": prof.get("profile_home_defense", 0.0),
                        "away_attack": prof.get("profile_away_attack", 0.0),
                        "away_defense": prof.get("profile_away_defense", 0.0),
                        "home_form": prof.get("profile_home_form", 0.0),
                        "away_form": prof.get("profile_away_form", 0.0),
                        "draw_adjustment": prof.get("profile_draw_adjustment", 0.0),
                        "risk_flags": prof.get("profile_risk_flags", []),
                        "profile_available": prof.get("profile_available", False),
                    },
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
            market_probs=match_market,
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
                    "home_elo": ratings[match.home_team_id].elo,
                    "away_elo": ratings[match.away_team_id].elo,
                    "fifa_rank_delta": fifa_rank_delta,
                    "elo_closeness": elo_closeness,
                    "is_group_stage": is_group,
                },
                model_version=sp.model_version,
            )
            session.add(shadow_pred)

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
    return {team.id: by_team[team.id][0] for team in teams}


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

    # Provider agreement: real agreement score based on actual prediction
    # distributions across market + AI providers, falling back to a count-based
    # heuristic when no per-match predictions exist yet.
    provider_agree = _compute_provider_agreement(session, teams)
    if provider_agree is None:
        # Fallback: count-based heuristic (single provider = 1.0,
        # multiple = slight boost but capped).
        provider_count = session.scalar(
            select(func.count(DataSnapshot.provider.distinct()))
            .where(DataSnapshot.status == "available")
        ) or 0
        provider_agree = min(1.0, 0.8 + 0.1 * provider_count) if provider_count > 0 else 0.5

    return freshness, ranking_coverage, provider_agree


def _compute_provider_agreement(
    session: Session, teams: list[Team]
) -> float | None:
    """Compute real provider agreement from market + AI predictions.

    For each match that has at least two provider predictions, compute a
    similarity score = 1 - normalized_max_min_gap on the predicted outcome
    distribution. Average across all such matches.

    Returns None when there are not enough multi-provider matches to compute
    a meaningful agreement score (caller falls back to count-based heuristic).
    """
    # Collect per-match prediction vectors from market snapshots.
    # Order by fetched_at DESC so the latest snapshot per match is first;
    # we use setdefault to keep only the first (latest) row per match.
    market_rows = session.execute(
        select(
            MarketSnapshot.match_id,
            MarketSnapshot.home_probability,
            MarketSnapshot.draw_probability,
            MarketSnapshot.away_probability,
        )
        .where(MarketSnapshot.provider == "sporttery")
        .order_by(MarketSnapshot.fetched_at.desc())
    ).all()
    market_by_match: dict[str, tuple[float, float, float]] = {}
    for row in market_rows:
        mid, h, d, a = row
        if h is None or d is None or a is None:
            continue
        market_by_match.setdefault(mid, (float(h), float(d), float(a)))

    # Collect per-match AI prediction vectors (use latest per match).
    # Order by created_at DESC so the most recent prediction per match is
    # first; setdefault ensures deterministic "latest wins" behavior.
    ai_rows = session.execute(
        select(
            AIPrediction.match_id,
            AIPrediction.parsed_home_win,
            AIPrediction.parsed_draw,
            AIPrediction.parsed_away_win,
        )
        .where(AIPrediction.parsed_home_win.is_not(None))
        .where(AIPrediction.parsed_draw.is_not(None))
        .where(AIPrediction.parsed_away_win.is_not(None))
        .order_by(AIPrediction.created_at.desc())
    ).all()
    ai_by_match: dict[str, tuple[float, float, float]] = {}
    for row in ai_rows:
        mid, h, d, a = row
        if h is None or d is None or a is None:
            continue
        ai_by_match.setdefault(mid, (float(h), float(d), float(a)))

    if not market_by_match and not ai_by_match:
        return None

    # Compute average agreement across matches with at least 2 providers.
    agreement_scores: list[float] = []
    all_match_ids = set(market_by_match.keys()) | set(ai_by_match.keys())
    for mid in all_match_ids:
        vectors: list[tuple[float, float, float]] = []
        if mid in market_by_match:
            vectors.append(market_by_match[mid])
        if mid in ai_by_match:
            vectors.append(ai_by_match[mid])
        if len(vectors) < 2:
            continue
        # Agreement = 1 - max(|h_a - h_b|, |d_a - d_b|, |a_a - a_b|).
        # Max abs gap ranges 0..1; converting to [0, 1] agreement scale.
        v1, v2 = vectors[0], vectors[1]
        max_gap = max(
            abs(v1[0] - v2[0]),
            abs(v1[1] - v2[1]),
            abs(v1[2] - v2[2]),
        )
        agreement_scores.append(max(0.0, 1.0 - max_gap))

    if not agreement_scores:
        return None
    # Average agreement, floored at 0.5 (single-provider agreement is
    # at least neutral, never worse).
    return max(0.5, sum(agreement_scores) / len(agreement_scores))
