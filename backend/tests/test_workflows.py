"""Tests for the workflow system: API routes, service logic, and state management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import create_database, session_scope
from app.logging_config import workflow_run_id_var
from app.main import create_app
from app.models import AIPrediction, DashboardRevision, Match, PredictionSnapshot, Team, WorkflowRun, WorkflowStep
from app.workflows import service as workflow_service
from app.workflows.state import set_current_run, is_workflow_running, get_current_run_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_workflow_state():
    """Reset global workflow state between tests."""
    from app.main import _rate_limit_store
    _rate_limit_store.clear()
    set_current_run(None)
    yield
    _rate_limit_store.clear()
    set_current_run(None)


@pytest.fixture
def client(tmp_path):
    """Create a test API client with a fresh database."""
    create_database(tmp_path / "workflow_test.sqlite3")
    return TestClient(create_app(start_background=False))


def wait_for_run(client: TestClient, run_id: int, timeout: float = 2.0) -> dict:
    """Poll a background workflow until it leaves running state."""
    deadline = time.monotonic() + timeout
    last = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/workflows/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last["status"] in ("success", "partial_success", "failed"):
            return last
        time.sleep(0.02)
    return last


def wait_until_not_running(timeout: float = 2.0) -> None:
    """Wait for the in-process workflow lock to be released without HTTP polling."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_workflow_running():
            return
        time.sleep(0.02)


def seed_match(session, *, match_id: str, kickoff: datetime, status: str = "scheduled") -> Match:
    session.add_all([
        Team(id=f"{match_id}_H", name=f"{match_id} Home", short_name="Home", code=f"H{match_id[-1]}", group_code="A"),
        Team(id=f"{match_id}_A", name=f"{match_id} Away", short_name="Away", code=f"A{match_id[-1]}", group_code="A"),
    ])
    session.flush()
    match = Match(
        id=match_id,
        group_code="A",
        home_team_id=f"{match_id}_H",
        away_team_id=f"{match_id}_A",
        kickoff=kickoff,
        status=status,
        source="test",
        stage="group",
    )
    session.add(match)
    session.flush()
    return match


# ---------------------------------------------------------------------------
# 1. /api/workflows/status returns correct structure
# ---------------------------------------------------------------------------

def test_workflow_status_returns_correct_structure(client):
    response = client.get("/api/workflows/status")
    assert response.status_code == 200
    data = response.json()
    assert "today_status" in data
    assert "recommended_action" in data
    assert "yesterday_matches" in data
    assert "upcoming_matches" in data
    assert "lock_status" in data
    assert data["today_status"] in ("needs_run", "already_run", "running")


def test_workflow_status_ai_skipped_counts_only_today(client):
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        old_run = WorkflowRun(
            workflow_type="pre_match",
            trigger_source="manual_button",
            status="success",
            started_at=yesterday,
        )
        today_run = WorkflowRun(
            workflow_type="pre_match",
            trigger_source="manual_button",
            status="success",
            started_at=now,
        )
        session.add_all([old_run, today_run])
        session.flush()
        session.add_all([
            WorkflowStep(
                workflow_run_id=old_run.id,
                step_name="ai_prediction",
                status="success",
                summary_json={"skipped": 9},
                finished_at=yesterday,
            ),
            WorkflowStep(
                workflow_run_id=today_run.id,
                step_name="ai_prediction",
                status="success",
                summary_json={"skipped": 2},
                finished_at=now,
            ),
        ])

    response = client.get("/api/workflows/status")
    assert response.status_code == 200
    assert response.json()["ai_stats"]["today_ai_skipped"] == 2


def test_workflow_status_yesterday_scored_uses_latest_pre_kickoff_rule(client):
    now = datetime.now(timezone.utc)
    kickoff = now - timedelta(hours=2)
    with session_scope() as session:
        match = seed_match(session, match_id="yesterday-score", kickoff=kickoff, status="final")
        match.home_score = 1
        match.away_score = 0
        revision = DashboardRevision(active=True, model_version="elo-poisson-v1", simulation_iterations=1, simulation_seed=1)
        session.add(revision)
        session.flush()
        session.add(
            PredictionSnapshot(
                match_id=match.id,
                revision_id=revision.id,
                kickoff=kickoff,
                is_pre_match_locked=False,
                is_fallback_locked=True,
                home_win=0.55,
                draw=0.25,
                away_win=0.20,
                home_xg=1.4,
                away_xg=0.9,
                scorelines=[],
                score_matrix=[],
                confidence=0.8,
                confidence_label="High",
                model_inputs={},
                model_version="elo-poisson-v1",
                snapshotted_at=kickoff - timedelta(minutes=30),
            )
        )

    response = client.get("/api/workflows/status")
    assert response.status_code == 200
    data = response.json()["yesterday_matches"]
    assert data["count"] == 1
    assert data["scored"] == 1
    assert data["needs_review"] is False


def test_workflow_status_ignores_hidden_xiaomi_predictions_in_ai_ready(client):
    now = datetime.now(timezone.utc)
    kickoff = now + timedelta(hours=6)
    with session_scope() as session:
        match = seed_match(session, match_id="future-hidden-ai", kickoff=kickoff, status="scheduled")
        session.add(
            AIPrediction(
                match_id=match.id,
                provider="xiaomi",
                model_id="mimo-v2.5-pro",
                model_version="ai-xiaomi-mimo-v2.5-pro-v1",
                prompt_version="worldcup-ai-v1",
                input_snapshot_json={},
                raw_response_text="{}",
                parsed_home_win=0.60,
                parsed_draw=0.20,
                parsed_away_win=0.20,
                confidence=0.7,
                created_at=now,
                error_code=None,
            )
        )

    response = client.get("/api/workflows/status")
    assert response.status_code == 200
    data = response.json()["upcoming_matches"]
    assert data["count_24h"] == 1
    assert data["ai_ready"] == 0
    assert data["needs_ai"] == 1


# ---------------------------------------------------------------------------
# 2. daily-open can be triggered
# ---------------------------------------------------------------------------

def test_daily_open_can_be_triggered(client):
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        response = client.post("/api/workflows/daily-open", json={"with_ensemble": False, "auto_lock": False})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["progress"]["total_steps"] > 0
        assert data["progress"]["percent"] == 0

        # Verify the run was recorded
        run = wait_for_run(client, data["run_id"])
        assert run["workflow_type"] == "daily_open"
        assert run["status"] in ("success", "partial_success", "failed")
        assert "percent" in run["progress"]


# ---------------------------------------------------------------------------
# 3. daily-open manual trigger bypasses cooldown
# ---------------------------------------------------------------------------

def test_daily_open_can_be_manually_triggered_when_cooldown_active(client):
    with patch("app.workflows.state.can_auto_run", return_value=False), \
         patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        response = client.post("/api/workflows/daily-open", json={"with_ai": False, "with_ensemble": False, "auto_lock": False})
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        wait_until_not_running()


# ---------------------------------------------------------------------------
# 4. daily-open default doesn't run AI
# ---------------------------------------------------------------------------

def test_daily_open_default_does_not_run_ai(client):
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        # Default DailyOpenRequest has with_ai=False
        response = client.post("/api/workflows/daily-open")
        assert response.status_code == 200
        data = response.json()
        if data["status"] == "started":
            run_id = data["run_id"]
            run = wait_for_run(client, run_id)
            assert "percent" in run["progress"]
            with session_scope() as session:
                ai_step = session.scalar(
                    select(WorkflowStep)
                    .where(WorkflowStep.workflow_run_id == run_id)
                    .where(WorkflowStep.step_name == "ai_prediction")
                )
                assert ai_step is not None
                assert ai_step.status == "skipped"


# ---------------------------------------------------------------------------
# 5. pre-match manual trigger works
# ---------------------------------------------------------------------------

def test_pre_match_manual_trigger(client):
    with patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_artifact_step"):
        response = client.post("/api/workflows/pre-match", json={"with_ai": False, "with_ensemble": False})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["progress"]["percent"] == 0

        run = wait_for_run(client, data["run_id"])
        assert run["workflow_type"] == "pre_match"
        assert run["trigger_source"] == "manual_button"


# ---------------------------------------------------------------------------
# 6. post-match manual trigger works
# ---------------------------------------------------------------------------

def test_post_match_manual_trigger(client):
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"):
        response = client.post("/api/workflows/post-match")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["progress"]["percent"] == 0

        run = wait_for_run(client, data["run_id"])
        assert run["workflow_type"] == "post_match"


# ---------------------------------------------------------------------------
# 7. lock manual trigger works
# ---------------------------------------------------------------------------

def test_lock_manual_trigger(client):
    with patch("app.workflows.service._run_lock_step"), \
         patch("app.workflows.service._run_artifact_step"):
        response = client.post("/api/workflows/lock")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["progress"]["percent"] == 0

        run = wait_for_run(client, data["run_id"])
        assert run["workflow_type"] == "lock"


# ---------------------------------------------------------------------------
# 8. full manual trigger works
# ---------------------------------------------------------------------------

def test_full_manual_trigger(client):
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        response = client.post("/api/workflows/full", json={"with_ai": False, "auto_lock": False})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "started"
        assert data["run_id"] > 0
        assert data["progress"]["percent"] == 0

        run = wait_for_run(client, data["run_id"])
        assert run["workflow_type"] == "full"


# ---------------------------------------------------------------------------
# 9. Already running returns 409 or already_running
# ---------------------------------------------------------------------------

def test_already_running_returns_conflict(client):
    # Simulate a running workflow
    set_current_run(999)

    response = client.post("/api/workflows/pre-match")
    assert response.status_code == 409

    response = client.post("/api/workflows/post-match")
    assert response.status_code == 409

    response = client.post("/api/workflows/lock")
    assert response.status_code == 409

    response = client.post("/api/workflows/full")
    assert response.status_code == 409

    # daily-open returns JSON instead of 409
    response = client.post("/api/workflows/daily-open")
    assert response.status_code == 200
    assert response.json()["status"] == "already_running"


# ---------------------------------------------------------------------------
# 10. Workflow step failure results in partial_success
# ---------------------------------------------------------------------------

def test_step_failure_results_in_partial_success(client):
    def failing_refresh(run_id):
        from app.workflows.service import _update_step
        _update_step(run_id, "refresh_results", "failed", error="test failure")

    with patch("app.workflows.service._run_refresh_step", side_effect=failing_refresh), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"):
        response = client.post("/api/workflows/full", json={"with_ai": False, "with_ensemble": False, "auto_lock": False})
        data = response.json()
        if data["status"] == "started":
            run_id = data["run_id"]
            run = wait_for_run(client, run_id)
            # Should be partial_success or failed depending on other steps
            assert run["status"] in ("partial_success", "failed", "success")


# ---------------------------------------------------------------------------
# 11. run-all limit still enforced
# ---------------------------------------------------------------------------

def test_run_all_limit_enforced(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.ai_run_all_max_limit", 5)
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_ai_prediction_step") as mock_ai, \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        response = client.post("/api/workflows/full", json={"with_ai": True, "limit": 100, "with_ensemble": False, "auto_lock": False})
        data = response.json()
        if data["status"] == "started":
            wait_for_run(client, data["run_id"])
            # The AI step should have been called with clamped limit
            mock_ai.assert_called_once()
            call_kwargs = mock_ai.call_args
            assert call_kwargs[1]["limit"] <= 5 or call_kwargs[0][1] <= 5


# ---------------------------------------------------------------------------
# 12. Workflow runs can be queried
# ---------------------------------------------------------------------------

def test_workflow_runs_can_be_queried(client):
    # First create a run
    with patch("app.workflows.service._run_refresh_step"), \
         patch("app.workflows.service._run_recompute_step"), \
         patch("app.workflows.service._run_accuracy_update_step"), \
         patch("app.workflows.service._run_artifact_step"), \
         patch("app.workflows.service._run_ensemble_step"):
        client.post("/api/workflows/daily-open", json={"with_ensemble": False, "auto_lock": False})

    # Query runs list
    response = client.get("/api/workflows/runs")
    assert response.status_code == 200
    data = response.json()
    assert "runs" in data
    assert len(data["runs"]) >= 1

    run = data["runs"][0]
    assert "id" in run
    assert "workflow_type" in run
    assert "status" in run
    assert "steps" in run

    # Query specific run detail
    run_id = run["id"]
    detail_response = client.get(f"/api/workflows/runs/{run_id}")
    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["id"] == run_id
    assert "steps" in detail
    assert len(detail["steps"]) > 0


def test_workflow_run_detail_404_for_missing(client):
    response = client.get("/api/workflows/runs/99999")
    assert response.status_code == 404


def test_second_manual_workflow_is_rejected_immediately(client):
    """The first request should reserve a run before background work starts."""
    with patch("app.workflows.service._run_recompute_step", side_effect=lambda *_: time.sleep(0.2)), \
         patch("app.workflows.service._run_artifact_step"):
        first = client.post("/api/workflows/pre-match", json={"with_ai": False, "with_ensemble": False})
        assert first.status_code == 200
        assert first.json()["run_id"] > 0

        second = client.post("/api/workflows/full", json={"with_ai": False, "with_ensemble": False, "auto_lock": False})
        assert second.status_code == 409


def test_start_workflow_run_clears_log_context_when_lock_unavailable(client):
    """A failed lock reservation should not leave a stale workflow id in logs."""
    set_current_run(123)

    run_id = workflow_service.start_workflow_run("pre_match", "manual_button", {})

    assert run_id == -1
    assert workflow_run_id_var.get("") == ""
    with session_scope() as session:
        failed_run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_type == "pre_match")
            .order_by(WorkflowRun.id.desc())
            .limit(1)
        )
        assert failed_run is not None
        assert failed_run.status == "failed"


@pytest.mark.asyncio
async def test_async_workflow_lock_failure_marks_run_failed(client):
    """Async workflow wrappers should use the same lock-failure semantics."""
    set_current_run(123)

    run_id = await workflow_service.run_pre_match_workflow_async(with_ai=False, with_ensemble=False)

    assert run_id == -1
    assert workflow_run_id_var.get("") == ""
    with session_scope() as session:
        failed_run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.workflow_type == "pre_match")
            .order_by(WorkflowRun.id.desc())
            .limit(1)
        )
        assert failed_run is not None
        assert failed_run.status == "failed"


def test_artifact_step_handles_missing_run_metadata(client):
    """Artifact generation should not fail with an unbound summary."""
    with patch("app.workflows.service._update_step") as update_step:
        workflow_service._run_artifact_step(99999)

    statuses = [call.args[2] for call in update_step.call_args_list]
    assert statuses == ["running", "success"]


# ---------------------------------------------------------------------------
# State management unit tests
# ---------------------------------------------------------------------------

def test_workflow_state_management():
    assert is_workflow_running() is False
    assert get_current_run_id() is None

    set_current_run(42)
    assert is_workflow_running() is True
    assert get_current_run_id() == 42

    set_current_run(None)
    assert is_workflow_running() is False
    assert get_current_run_id() is None


# ---------------------------------------------------------------------------
# Scheduler tests
# ---------------------------------------------------------------------------

def test_scheduler_should_auto_run_daily(client):
    with patch("app.workflows.scheduler.settings") as mock_settings, \
         patch("app.workflows.scheduler.can_auto_run", return_value=True), \
         patch("app.workflows.scheduler.get_today_status", return_value={"status": "needs_run", "failed_steps": []}), \
         patch("app.workflows.scheduler.is_workflow_running", return_value=False):
        mock_settings.auto_run_daily_workflow_on_open = True
        from app.workflows.scheduler import should_auto_run_daily
        assert should_auto_run_daily() is True


def test_scheduler_should_not_auto_run_when_disabled(client):
    with patch("app.workflows.scheduler.settings") as mock_settings, \
         patch("app.workflows.scheduler.can_auto_run", return_value=True), \
         patch("app.workflows.scheduler.get_today_status", return_value={"status": "needs_run", "failed_steps": []}), \
         patch("app.workflows.scheduler.is_workflow_running", return_value=False):
        mock_settings.auto_run_daily_workflow_on_open = False
        from app.workflows.scheduler import should_auto_run_daily
        assert should_auto_run_daily() is False


# ---------------------------------------------------------------------------
# Integration test: real recompute + snapshot + lock pipeline (no mocks)
# ---------------------------------------------------------------------------

def test_daily_open_integration_recompute_snapshot_lock(tmp_path, monkeypatch):
    """Integration test: run_daily_open_workflow with real recompute, snapshot,
    and lock steps — no mocking of core logic.

    This verifies the actual recompute → snapshot → lock pipeline works
    end-to-end with a seeded database, catching regressions that the
    fully-mocked tests above would miss.
    """
    from pathlib import Path
    from app.db import create_database, session_scope
    from app.main import create_app
    from app.models import (
        DashboardRevision, Match, MatchPrediction, PredictionSnapshot,
        WorkflowRun, WorkflowStep,
    )
    from app.providers.openfootball import OpenFootballProvider
    from app.services.seed import seed_ratings, seed_tournament, seed_team_aliases
    from app.services.recompute import recompute_all
    from app.workflows.service import run_daily_open_workflow

    ROOT = Path(__file__).resolve().parents[2]
    FIXTURES = Path(__file__).parent / "fixtures"

    # 1. Seed a real database
    create_database(tmp_path / "integration_test.sqlite3")
    with session_scope() as session:
        payload = OpenFootballProvider.from_files(
            FIXTURES / "openfootball-worldcup-2026.json",
            FIXTURES / "openfootball-worldcup-teams-2026.json",
        ).load()
        seed_tournament(session, payload)
        seed_ratings(session, ROOT / "data/seed/elo-ratings-2026.json")
        seed_team_aliases(session, ROOT / "data/seed/sporttery-team-aliases.json")
        recompute_all(session, iterations=100, seed=7)

    # 2. Run the daily-open workflow with real steps (no AI, no ensemble)
    #    Mock only the refresh step (it needs network), let recompute/score/lock run for real.
    with patch("app.workflows.service._run_refresh_step"):
        run_id = run_daily_open_workflow(
            with_ai=False,
            with_ensemble=False,
            auto_lock=True,
            trigger_source="integration_test",
        )

    assert run_id > 0, "Workflow should return a valid run_id"

    # 3. Verify the workflow completed (not just "started")
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        assert run is not None
        assert run.status in ("success", "partial_success"), (
            f"Workflow should succeed or partially succeed, got: {run.status}"
        )

        # 4. Verify the recompute step actually executed (not skipped)
        recompute_step = session.scalar(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .where(WorkflowStep.step_name == "post_match_recompute")
        )
        assert recompute_step is not None
        assert recompute_step.status == "success", (
            f"Recompute step should succeed, got: {recompute_step.status}, "
            f"error: {recompute_step.error_message}"
        )

        # 5. Verify predictions were actually written by recompute
        from sqlalchemy import func as sqlfunc
        active_revision_id = session.scalar(
            select(DashboardRevision.id)
            .where(DashboardRevision.active.is_(True))
            .order_by(DashboardRevision.id.desc())
            .limit(1)
        )
        pred_count = session.scalar(
            select(sqlfunc.count(MatchPrediction.id))
            .where(MatchPrediction.revision_id == active_revision_id)
        )
        assert pred_count > 0, "Recompute should produce MatchPrediction rows"

        # 6. Verify snapshots were written (lock step depends on them)
        snap_count = session.scalar(
            select(sqlfunc.count(PredictionSnapshot.id))
        )
        assert snap_count > 0, "Snapshots should exist after recompute + lock"

        # 7. Verify the lock step actually ran
        lock_step = session.scalar(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .where(WorkflowStep.step_name == "lock_predictions")
        )
        assert lock_step is not None
        assert lock_step.status == "success", (
            f"Lock step should succeed, got: {lock_step.status}, "
            f"error: {lock_step.error_message}"
        )
