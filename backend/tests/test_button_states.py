"""Tests for button_states in the workflow status API."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.db import create_database
from app.main import create_app
from app.workflows.state import set_current_run
from app.workflows.service import _check_ai_available, _compute_button_states


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
    create_database(tmp_path / "button_test.sqlite3")
    return TestClient(create_app(start_background=False))


def _default_upcoming(needs_ai=0):
    return {"count_24h": 0, "count_48h": 0, "baseline_ready": 0, "ai_ready": 0, "ensemble_ready": 0, "needs_ai": needs_ai}


def _default_yesterday(count=0, needs_review=False):
    return {"count": count, "scored": 0, "needs_review": needs_review}


def _default_lock(needs_lock=0):
    return {"matches_near_kickoff": 0, "locked": 0, "needs_lock": needs_lock, "real_time_only": 0}


# ---------------------------------------------------------------------------
# 1. /api/workflows/status returns button_states
# ---------------------------------------------------------------------------

def test_workflow_status_returns_button_states(client):
    response = client.get("/api/workflows/status")
    assert response.status_code == 200
    data = response.json()
    assert "button_states" in data
    bs = data["button_states"]
    for key in ("daily_open", "pre_match", "ai_prediction", "post_match", "lock", "full"):
        assert key in bs, f"Missing button: {key}"
        assert "enabled" in bs[key], f"Missing 'enabled' in {key}"
        assert "reason" in bs[key], f"Missing 'reason' in {key}"


# ---------------------------------------------------------------------------
# 2. button_states explains AI unavailable reason when no key
# ---------------------------------------------------------------------------

def test_ai_button_unavailable_when_no_key():
    with patch("app.workflows.service._check_ai_available", return_value=(False, "未配置 API Key（deepseek, xiaomi）")):
        states = _compute_button_states(
            running=False,
            cooldown_active=False,
            upcoming_info=_default_upcoming(needs_ai=5),
            yesterday_info=_default_yesterday(),
            lock_info=_default_lock(),
        )
    ai = states["ai_prediction"]
    assert ai["enabled"] is False
    assert "API Key" in ai["reason"] or "未配置" in ai["reason"]


# ---------------------------------------------------------------------------
# 3. AI button enabled when key configured and needs_ai > 0
# ---------------------------------------------------------------------------

def test_ai_button_enabled_when_key_and_needs_ai():
    with patch("app.workflows.service._check_ai_available", return_value=(True, "AI 可用")), \
         patch("app.ai.model_registry.list_enabled_models", return_value=[MagicMock(), MagicMock()]), \
         patch("app.config.settings.ai_run_all_max_limit", 20):
        states = _compute_button_states(
            running=False,
            cooldown_active=False,
            upcoming_info=_default_upcoming(needs_ai=5),
            yesterday_info=_default_yesterday(),
            lock_info=_default_lock(),
        )
    ai = states["ai_prediction"]
    assert ai["enabled"] is True
    assert ai["needs_ai"] == 5
    assert "可运行" in ai["reason"]


# ---------------------------------------------------------------------------
# 4. All buttons disabled when workflow running
# ---------------------------------------------------------------------------

def test_all_buttons_disabled_when_workflow_running():
    with patch("app.workflows.service._check_ai_available", return_value=(True, "AI 可用")), \
         patch("app.ai.model_registry.list_enabled_models", return_value=[MagicMock()]):
        states = _compute_button_states(
            running=True,
            cooldown_active=False,
            upcoming_info=_default_upcoming(needs_ai=5),
            yesterday_info=_default_yesterday(count=3, needs_review=True),
            lock_info=_default_lock(needs_lock=2),
        )
    for key in ("daily_open", "pre_match", "ai_prediction", "post_match", "lock", "full"):
        assert states[key]["enabled"] is False, f"{key} should be disabled when running"
        assert "正在运行" in states[key]["reason"], f"{key} reason should mention running"


# ---------------------------------------------------------------------------
# 5. estimated_ai_calls calculated correctly
# ---------------------------------------------------------------------------

def test_estimated_ai_calls_calculated_correctly():
    mock_models = [MagicMock(), MagicMock(), MagicMock()]  # 3 enabled models
    with patch("app.workflows.service._check_ai_available", return_value=(True, "AI 可用")), \
         patch("app.ai.model_registry.list_enabled_models", return_value=mock_models), \
         patch("app.config.settings.ai_run_all_max_limit", 20):
        # needs_ai=5, 3 models => estimated = min(5, 20) * 3 = 15
        states = _compute_button_states(
            running=False,
            cooldown_active=False,
            upcoming_info=_default_upcoming(needs_ai=5),
            yesterday_info=_default_yesterday(),
            lock_info=_default_lock(),
        )
    assert states["ai_prediction"]["estimated_calls"] == 15
    assert states["full"]["estimated_calls"] == 15


def test_estimated_ai_calls_capped_by_limit():
    mock_models = [MagicMock(), MagicMock()]  # 2 enabled models
    with patch("app.workflows.service._check_ai_available", return_value=(True, "AI 可用")), \
         patch("app.ai.model_registry.list_enabled_models", return_value=mock_models), \
         patch("app.config.settings.ai_run_all_max_limit", 3):
        # needs_ai=10, limit=3, 2 models => estimated = min(10, 3) * 2 = 6
        states = _compute_button_states(
            running=False,
            cooldown_active=False,
            upcoming_info=_default_upcoming(needs_ai=10),
            yesterday_info=_default_yesterday(),
            lock_info=_default_lock(),
        )
    assert states["ai_prediction"]["estimated_calls"] == 6


# ---------------------------------------------------------------------------
# 6. Manual buttons not affected by cooldown
# ---------------------------------------------------------------------------

def test_manual_buttons_not_affected_by_cooldown():
    with patch("app.workflows.service._check_ai_available", return_value=(True, "AI 可用")), \
         patch("app.ai.model_registry.list_enabled_models", return_value=[MagicMock()]):
        states = _compute_button_states(
            running=False,
            cooldown_active=True,  # cooldown active
            upcoming_info=_default_upcoming(needs_ai=3),
            yesterday_info=_default_yesterday(count=2, needs_review=True),
            lock_info=_default_lock(needs_lock=1),
        )
    # daily_open is disabled by cooldown
    assert states["daily_open"]["enabled"] is False
    assert "冷却" in states["daily_open"]["reason"]

    # Manual buttons should still be enabled
    assert states["pre_match"]["enabled"] is True
    assert states["full"]["enabled"] is True
    assert states["ai_prediction"]["enabled"] is True
    assert states["post_match"]["enabled"] is True
    assert states["lock"]["enabled"] is True
