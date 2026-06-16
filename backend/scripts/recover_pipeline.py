#!/usr/bin/env python3
"""
Prediction Pipeline Recovery Script

Restores the prediction pipeline by:
1. Seeding the matches table from world-cup-2026.json
2. Seeding Elo ratings
3. Running recompute_all() to generate predictions
4. Locking due prediction snapshots
5. Purging invalid post-kickoff snapshots for completed matches

BOUNDARY RULES:
- Only generates predictions for matches with status != "final"
- Only locks snapshots for matches where now <= kickoff and kickoff - now <= 24h
- Does NOT create pre-match snapshots for already-completed matches
- Purges any post-kickoff snapshots for completed matches after recompute
- Completed matches are marked as not_scorable_no_snapshot

DOES NOT:
- Train any model
- Modify Ensemble weights
- Backfill predictions for completed matches
- Connect factor research Demo
"""

import sys
import os
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Ensure backend is on the path
BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

# Load .env from project root
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from sqlalchemy import func, select, delete as sa_delete
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.db import create_database, session_scope
from app.models import (
    DashboardRevision, Match, Team, TeamProfile,
    MatchPrediction, PredictionSnapshot
)
from app.schemas import TournamentPayload
from app.services.seed import seed_ratings, seed_team_aliases, seed_tournament
from app.services.recompute import recompute_all
from app.services.snapshots import lock_due_predictions, repair_invalid_prediction_locks

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def purge_invalid_post_kickoff_snapshots(session: Session) -> int:
    """Delete snapshots for completed matches where snapshotted_at >= kickoff.
    
    These are invalid because they were created after the match started,
    and must not be used for scoring.
    """
    now = datetime.now(timezone.utc)
    purged = 0
    
    completed_matches = list(session.scalars(
        select(Match).where(Match.status == "final")
    ))
    
    for m in completed_matches:
        kickoff = m.kickoff
        if kickoff.tzinfo is None:
            kickoff = kickoff.replace(tzinfo=timezone.utc)
        
        invalid_snaps = list(session.scalars(
            select(PredictionSnapshot)
            .where(PredictionSnapshot.match_id == m.id)
            .where(PredictionSnapshot.snapshotted_at >= kickoff)
        ))
        
        for snap in invalid_snaps:
            logger.warning(
                f"  PURGING invalid post-kickoff snapshot: match={m.id} "
                f"snap_id={snap.id} model={snap.model_version} "
                f"snapshotted_at={snap.snapshotted_at} kickoff={kickoff}"
            )
            session.delete(snap)
            purged += 1
    
    return purged


def main():
    logger.info("=" * 60)
    logger.info("PREDICTION PIPELINE RECOVERY")
    logger.info("=" * 60)
    logger.info(f"Database path: {settings.database_path}")
    logger.info(f"Database exists: {settings.database_path.exists()}")
    logger.info(f"Project root: {PROJECT_ROOT}")

    # Step 0: Create database schema if needed
    logger.info("\n--- Step 0: Create database schema ---")
    create_database()

    # Step 1: Check current state
    logger.info("\n--- Step 1: Current state ---")
    with session_scope() as session:
        n_teams = session.scalar(select(func.count(Team.id)))
        n_matches = session.scalar(select(func.count(Match.id)))
        n_predictions = session.scalar(select(func.count(MatchPrediction.id)))
        n_snapshots = session.scalar(select(func.count(PredictionSnapshot.id)))
        n_revisions = session.scalar(select(func.count(DashboardRevision.id)))
        n_profiles = session.scalar(select(func.count(TeamProfile.id)))

        logger.info(f"  teams: {n_teams}")
        logger.info(f"  matches: {n_matches}")
        logger.info(f"  match_predictions: {n_predictions}")
        logger.info(f"  prediction_snapshots: {n_snapshots}")
        logger.info(f"  dashboard_revisions: {n_revisions}")
        logger.info(f"  team_profiles: {n_profiles}")

    # Step 2: Seed tournament (matches + teams)
    logger.info("\n--- Step 2: Seed tournament ---")
    with session_scope() as session:
        team_count = session.scalar(select(func.count(Team.id)))
        if team_count == 0:
            seed_path = PROJECT_ROOT / "data" / "seed" / "world-cup-2026.json"
            logger.info(f"  Loading seed from: {seed_path}")
            logger.info(f"  Seed file exists: {seed_path.exists()}")
            payload = TournamentPayload.model_validate_json(seed_path.read_text(encoding="utf-8"))
            logger.info(f"  Payload: {len(payload.teams)} teams, {len(payload.matches)} matches")
            seed_tournament(session, payload)
            logger.info("  Seed tournament complete")
        else:
            logger.info(f"  Teams already exist ({team_count}), skipping seed")

    # Step 3: Seed ratings
    logger.info("\n--- Step 3: Seed ratings ---")
    with session_scope() as session:
        seed_ratings(session, PROJECT_ROOT / "data" / "seed" / "elo-ratings-2026.json")
        seed_team_aliases(session, PROJECT_ROOT / "data" / "seed" / "sporttery-team-aliases.json")
        logger.info("  Ratings and aliases seeded")

    # Step 4: Seed team profiles
    logger.info("\n--- Step 4: Seed team profiles ---")
    with session_scope() as session:
        profile_count = session.scalar(select(func.count(TeamProfile.id))) or 0
        if profile_count == 0:
            from app.team_profiles.service import rebuild_team_profiles
            rebuild_team_profiles(session, use_seed=True)
            logger.info("  Team profiles built")
        else:
            logger.info(f"  Team profiles already exist ({profile_count})")

    # Step 5: Recompute all predictions
    logger.info("\n--- Step 5: Recompute predictions ---")
    with session_scope() as session:
        active = session.scalar(
            select(DashboardRevision.id).where(DashboardRevision.active.is_(True)).limit(1)
        )
        if active is None:
            logger.info("  No active revision, running recompute_all()...")
            recompute_all(
                session,
                iterations=settings.simulation_iterations,
                seed=settings.simulation_seed,
            )
            logger.info("  Recompute complete")
        else:
            logger.info(f"  Active revision exists (id={active}), skipping recompute")

    # Step 6: Purge invalid post-kickoff snapshots for completed matches
    logger.info("\n--- Step 6: Purge invalid post-kickoff snapshots ---")
    with session_scope() as session:
        purged = purge_invalid_post_kickoff_snapshots(session)
        logger.info(f"  Purged {purged} invalid post-kickoff snapshots")

    # Step 7: Repair invalid locks and lock due predictions
    logger.info("\n--- Step 7: Lock due predictions ---")
    with session_scope() as session:
        repair_invalid_prediction_locks(session)
        lock_due_predictions(session)
        logger.info("  Lock due predictions complete")

    # Step 8: Verify final state
    logger.info("\n--- Step 8: Final state ---")
    with session_scope() as session:
        n_teams = session.scalar(select(func.count(Team.id)))
        n_matches = session.scalar(select(func.count(Match.id)))
        n_final = session.scalar(select(func.count(Match.id)).where(Match.status == "final"))
        n_scheduled = session.scalar(select(func.count(Match.id)).where(Match.status != "final"))
        n_predictions = session.scalar(select(func.count(MatchPrediction.id)))
        n_snapshots = session.scalar(select(func.count(PredictionSnapshot.id)))
        n_locked = session.scalar(
            select(func.count(PredictionSnapshot.id))
            .where(PredictionSnapshot.is_pre_match_locked.is_(True))
        )
        n_revisions = session.scalar(select(func.count(DashboardRevision.id)))

        logger.info(f"  teams: {n_teams}")
        logger.info(f"  matches: {n_matches} (final={n_final}, scheduled={n_scheduled})")
        logger.info(f"  match_predictions: {n_predictions}")
        logger.info(f"  prediction_snapshots: {n_snapshots} (locked={n_locked})")
        logger.info(f"  dashboard_revisions: {n_revisions}")

        # Check upcoming matches within 24h
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(hours=24)
        upcoming = list(session.scalars(
            select(Match)
            .where(Match.status != "final", Match.kickoff <= cutoff, Match.kickoff >= now)
            .order_by(Match.kickoff)
        ))
        logger.info(f"\n  Upcoming matches (next 24h): {len(upcoming)}")
        for m in upcoming:
            has_pred = session.scalar(
                select(func.count(MatchPrediction.id))
                .where(MatchPrediction.match_id == m.id)
            )
            has_snap = session.scalar(
                select(func.count(PredictionSnapshot.id))
                .where(
                    PredictionSnapshot.match_id == m.id,
                    PredictionSnapshot.is_pre_match_locked.is_(True),
                )
            )
            logger.info(f"    {m.id} | {m.kickoff} | predictions={has_pred} | locked_snapshots={has_snap}")

        # Verify completed matches have NO snapshots (all purged)
        logger.info("\n  Completed matches (should have 0 snapshots):")
        completed_matches = list(session.scalars(
            select(Match).where(Match.status == "final").order_by(Match.kickoff)
        ))
        for m in completed_matches:
            total_snaps = session.scalar(
                select(func.count(PredictionSnapshot.id))
                .where(PredictionSnapshot.match_id == m.id)
            )
            logger.info(f"    {m.id} | snapshots={total_snaps} | {'OK' if total_snaps == 0 else 'WARNING: should be 0'}")

    # Verification summary
    logger.info("\n" + "=" * 60)
    logger.info("VERIFICATION SUMMARY")
    logger.info("=" * 60)
    with session_scope() as session:
        n_matches = session.scalar(select(func.count(Match.id)))
        n_predictions = session.scalar(select(func.count(MatchPrediction.id)))
        n_snapshots = session.scalar(select(func.count(PredictionSnapshot.id)))
        
        # Verify no post-kickoff snapshots for completed matches
        from sqlalchemy import and_
        invalid_count = 0
        completed = list(session.scalars(select(Match).where(Match.status == "final")))
        for m in completed:
            kickoff = m.kickoff
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            cnt = session.scalar(
                select(func.count(PredictionSnapshot.id))
                .where(
                    PredictionSnapshot.match_id == m.id,
                    PredictionSnapshot.snapshotted_at >= kickoff,
                )
            )
            invalid_count += cnt

        checks = [
            ("matches > 0", n_matches > 0),
            ("match_predictions > 0", n_predictions > 0),
            ("prediction_snapshots > 0", n_snapshots > 0),
            ("no post-kickoff snapshots for completed matches", invalid_count == 0),
        ]
        for name, passed in checks:
            status = "PASS" if passed else "FAIL"
            logger.info(f"  [{status}] {name}")

        all_passed = all(c[1] for c in checks)
        if all_passed:
            logger.info("\n  ALL CHECKS PASSED - Pipeline recovered successfully")
        else:
            logger.info("\n  SOME CHECKS FAILED - See details above")
        return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
