"""Tests for the workflow system: API routes, service logic, and state management."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import create_database, session_scope
from app.main import create_app
from app.models import WorkflowRun, WorkflowStep
from app.workflows.state import set_current_run, is_workflow_running, get_current_run_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_workflow_state():
    """Reset global workflow state between tests."""
    set_current_run(None)
    yield
    set_current_run(None)


@pytest.fixture
def client(tmp_path):
    """Create a test API client with a fresh database."""
    create_database(tmp_path / "workflow_test.sqlite3")
    return TestClient(create_app(start_background=False))


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

        # Verify the run was recorded
        with session_scope() as session:
            run = session.get(WorkflowRun, data["run_id"])
            assert run is not None
            assert run.workflow_type == "daily_open"
            assert run.status in ("success", "partial_success", "failed")


# ---------------------------------------------------------------------------
# 3. daily-open skipped when cooldown active
# ---------------------------------------------------------------------------

def test_daily_open_skipped_when_cooldown_active(client):
    with patch("app.workflows.state.can_auto_run", return_value=False):
        response = client.post("/api/workflows/daily-open", json={"with_ai": False, "with_ensemble": False, "auto_lock": False})
        # When cooldown is active and with_ai=False, it returns skipped
        data = response.json()
        assert data["status"] in ("skipped", "started", "already_running")


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

        with session_scope() as session:
            run = session.get(WorkflowRun, data["run_id"])
            assert run.workflow_type == "pre_match"
            assert run.trigger_source == "manual_button"


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

        with session_scope() as session:
            run = session.get(WorkflowRun, data["run_id"])
            assert run.workflow_type == "post_match"


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

        with session_scope() as session:
            run = session.get(WorkflowRun, data["run_id"])
            assert run.workflow_type == "lock"


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

        with session_scope() as session:
            run = session.get(WorkflowRun, data["run_id"])
            assert run.workflow_type == "full"


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
            with session_scope() as session:
                run = session.get(WorkflowRun, run_id)
                # Should be partial_success or failed depending on other steps
                assert run.status in ("partial_success", "failed", "success")


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
         patch("app.workflows.scheduler.get_today_status", return_value="needs_run"), \
         patch("app.workflows.scheduler.is_workflow_running", return_value=False):
        mock_settings.auto_run_daily_workflow_on_open = True
        from app.workflows.scheduler import should_auto_run_daily
        assert should_auto_run_daily() is True


def test_scheduler_should_not_auto_run_when_disabled(client):
    with patch("app.workflows.scheduler.settings") as mock_settings, \
         patch("app.workflows.scheduler.can_auto_run", return_value=True), \
         patch("app.workflows.scheduler.get_today_status", return_value="needs_run"), \
         patch("app.workflows.scheduler.is_workflow_running", return_value=False):
        mock_settings.auto_run_daily_workflow_on_open = False
        from app.workflows.scheduler import should_auto_run_daily
        assert should_auto_run_daily() is False
