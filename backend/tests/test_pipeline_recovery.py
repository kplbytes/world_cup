"""Regression tests for prediction snapshot pipeline recovery.

These tests verify that the prediction pipeline produces:
- matches > 0 after seed
- match_predictions > 0 after refresh
- prediction_snapshots with locked=True for T-24h matches
- completed matches without pre-match snapshots are not scorable
- no post-hoc snapshot backfill for completed matches
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

# Ensure backend is importable
BACKEND_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BACKEND_DIR))

# Load .env
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR / ".env")

from sqlalchemy import func, select

from app.config import settings
from app.db import session_scope
from app.models import (
    DashboardRevision, Match, MatchPrediction, PredictionSnapshot, Team
)


class TestPipelineSeed:
    """After seed, matches table should have data."""

    def test_matches_greater_than_zero(self):
        with session_scope() as session:
            count = session.scalar(select(func.count(Match.id)))
            assert count > 0, f"Expected matches > 0, got {count}"

    def test_teams_greater_than_zero(self):
        with session_scope() as session:
            count = session.scalar(select(func.count(Team.id)))
            assert count > 0, f"Expected teams > 0, got {count}"

    def test_matches_have_valid_ids(self):
        with session_scope() as session:
            matches = list(session.scalars(select(Match).limit(5)))
            for m in matches:
                assert m.id is not None
                assert m.kickoff is not None


class TestPipelinePredictions:
    """After refresh, match_predictions should have data."""

    def test_match_predictions_greater_than_zero(self):
        with session_scope() as session:
            count = session.scalar(select(func.count(MatchPrediction.id)))
            assert count > 0, f"Expected match_predictions > 0, got {count}"

    def test_scheduled_matches_have_predictions(self):
        with session_scope() as session:
            scheduled = list(session.scalars(
                select(Match).where(Match.status != "final").limit(5)
            ))
            for m in scheduled:
                pred_count = session.scalar(
                    select(func.count(MatchPrediction.id))
                    .where(MatchPrediction.match_id == m.id)
                )
                assert pred_count > 0, f"Match {m.id} has no predictions"

    def test_dashboard_revision_exists(self):
        with session_scope() as session:
            active = session.scalar(
                select(DashboardRevision.id)
                .where(DashboardRevision.active.is_(True))
                .limit(1)
            )
            assert active is not None, "No active dashboard revision"


class TestPipelineSnapshots:
    """T-24h matches should have locked prediction snapshots."""

    def test_prediction_snapshots_greater_than_zero(self):
        with session_scope() as session:
            count = session.scalar(select(func.count(PredictionSnapshot.id)))
            assert count > 0, f"Expected prediction_snapshots > 0, got {count}"

    def test_t24h_matches_have_locked_snapshots(self):
        """Matches within 24h of kickoff should have locked snapshots."""
        with session_scope() as session:
            now = datetime.now(timezone.utc)
            cutoff = now + timedelta(hours=24)
            upcoming = list(session.scalars(
                select(Match)
                .where(Match.status != "final", Match.kickoff <= cutoff, Match.kickoff >= now)
                .order_by(Match.kickoff)
            ))
            if not upcoming:
                pytest.skip("No matches within 24h to test")
            for m in upcoming:
                locked = session.scalar(
                    select(func.count(PredictionSnapshot.id))
                    .where(
                        PredictionSnapshot.match_id == m.id,
                        PredictionSnapshot.is_pre_match_locked.is_(True),
                    )
                )
                assert locked > 0, f"Match {m.id} (kickoff={m.kickoff}) has no locked snapshots"


class TestCompletedMatchesNoBackfill:
    """Completed matches must not have pre-match locked snapshots backfilled."""

    def test_completed_matches_no_pre_match_locked_snapshots(self):
        with session_scope() as session:
            completed = list(session.scalars(
                select(Match).where(Match.status == "final").order_by(Match.kickoff)
            ))
            if not completed:
                pytest.skip("No completed matches to test")
            for m in completed:
                locked = session.scalar(
                    select(func.count(PredictionSnapshot.id))
                    .where(
                        PredictionSnapshot.match_id == m.id,
                        PredictionSnapshot.is_pre_match_locked.is_(True),
                    )
                )
                assert locked == 0, (
                    f"Completed match {m.id} has {locked} pre-match locked snapshots - "
                    f"backfill is prohibited"
                )

    def test_completed_matches_marked_not_scorable(self):
        """Completed matches without pre-match snapshots are not scorable for Shadow."""
        with session_scope() as session:
            completed = list(session.scalars(
                select(Match).where(Match.status == "final").order_by(Match.kickoff)
            ))
            if not completed:
                pytest.skip("No completed matches to test")
            for m in completed:
                # A match is scorable only if it has pre-match locked snapshots
                locked = session.scalar(
                    select(func.count(PredictionSnapshot.id))
                    .where(
                        PredictionSnapshot.match_id == m.id,
                        PredictionSnapshot.is_pre_match_locked.is_(True),
                    )
                )
                scorable = locked > 0
                assert not scorable, (
                    f"Completed match {m.id} without pre-match snapshot should be not_scorable"
                )

    def test_completed_matches_no_post_kickoff_snapshots(self):
        """No snapshots for completed matches where snapshotted_at >= kickoff."""
        with session_scope() as session:
            completed = list(session.scalars(
                select(Match).where(Match.status == "final").order_by(Match.kickoff)
            ))
            if not completed:
                pytest.skip("No completed matches to test")
            for m in completed:
                kickoff = m.kickoff
                if kickoff.tzinfo is None:
                    kickoff = kickoff.replace(tzinfo=timezone.utc)
                post_kickoff = session.scalar(
                    select(func.count(PredictionSnapshot.id))
                    .where(
                        PredictionSnapshot.match_id == m.id,
                        PredictionSnapshot.snapshotted_at >= kickoff,
                    )
                )
                assert post_kickoff == 0, (
                    f"Completed match {m.id} has {post_kickoff} post-kickoff snapshots - "
                    f"recovery script must purge them"
                )

    def test_completed_matches_no_snapshots_at_all(self):
        """Completed matches should have zero snapshots after purge."""
        with session_scope() as session:
            completed = list(session.scalars(
                select(Match).where(Match.status == "final").order_by(Match.kickoff)
            ))
            if not completed:
                pytest.skip("No completed matches to test")
            for m in completed:
                total = session.scalar(
                    select(func.count(PredictionSnapshot.id))
                    .where(PredictionSnapshot.match_id == m.id)
                )
                assert total == 0, (
                    f"Completed match {m.id} has {total} snapshots - should be 0 after purge"
                )


class TestScoringOnlyUsesPreKickoffSnapshots:
    """model-score must only use snapshots where snapshotted_at < kickoff."""

    def test_scorable_matches_count_is_zero(self):
        """No completed matches should be scorable (all lack pre-kickoff snapshots)."""
        from app.services.scoring import _scorable_snapshot_rows
        with session_scope() as session:
            rows = _scorable_snapshot_rows(session)
            assert len(rows) == 0, (
                f"Expected 0 scorable matches, got {len(rows)} - "
                f"post-kickoff snapshots must not be scorable"
            )


class TestDatabasePath:
    """Verify database path is correctly configured."""

    def test_database_path_points_to_correct_file(self):
        # .env sets DATABASE_PATH=backend/world_cup.db (relative to project root)
        # settings.database_path is already resolved to absolute path
        assert "world_cup.db" in str(settings.database_path), (
            f"Database path doesn't point to world_cup.db: {settings.database_path}"
        )
        assert settings.database_path.exists(), (
            f"Database file doesn't exist: {settings.database_path}"
        )

    def test_database_file_exists(self):
        assert settings.database_path.exists(), (
            f"Database file not found: {settings.database_path}"
        )
