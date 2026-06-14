"""Tests that AI service functions read config from pydantic-settings consistently."""

from app.ai.service import get_ai_run_mode, get_prompt_version, is_ai_enabled
from app.config import settings


def test_is_ai_enabled_matches_settings():
    assert is_ai_enabled() == settings.enable_ai_prediction


def test_get_ai_run_mode_matches_settings():
    assert get_ai_run_mode() == settings.ai_run_mode


def test_get_prompt_version_matches_settings():
    assert get_prompt_version() == settings.ai_prompt_version


def test_is_ai_enabled_false(monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_prediction", False)
    assert is_ai_enabled() is False


def test_is_ai_enabled_true(monkeypatch):
    monkeypatch.setattr(settings, "enable_ai_prediction", True)
    assert is_ai_enabled() is True


def test_get_prompt_version_custom(monkeypatch):
    monkeypatch.setattr(settings, "ai_prompt_version", "worldcup-ai-v2")
    assert get_prompt_version() == "worldcup-ai-v2"


def test_get_ai_run_mode_custom(monkeypatch):
    monkeypatch.setattr(settings, "ai_run_mode", "auto")
    assert get_ai_run_mode() == "auto"
