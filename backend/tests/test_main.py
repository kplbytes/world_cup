from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import _is_live_window
from app.models import Match, Team


def _add_match(session, kickoff, status="scheduled"):
    session.add_all(
        [
            Team(id="AAA", name="Alpha", short_name="Alpha", code="AAA", group_code="A"),
            Team(id="BBB", name="Beta", short_name="Beta", code="BBB", group_code="A"),
        ]
    )
    session.flush()
    session.add(
        Match(
            id="match-1",
            group_code="A",
            home_team_id="AAA",
            away_team_id="BBB",
            kickoff=kickoff,
            status=status,
            source="test",
        )
    )
    session.flush()


def test_future_match_does_not_enable_live_refresh(db_session):
    now = datetime(2026, 6, 13, 8, tzinfo=timezone.utc)
    _add_match(db_session, now + timedelta(days=1))

    assert _is_live_window(db_session, now=now) is False


def test_recently_started_match_enables_live_refresh(db_session):
    now = datetime(2026, 6, 13, 8, tzinfo=timezone.utc)
    _add_match(db_session, now - timedelta(hours=1))

    assert _is_live_window(db_session, now=now) is True


def test_scheduler_skips_refresh_job_when_disabled(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "initialize_database", lambda: None)
    monkeypatch.setattr(main_mod.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(main_mod.settings, "enable_scheduled_refresh", False)

    app = main_mod.create_app(start_background=True)

    with TestClient(app):
        assert main_mod._scheduler is not None
        assert main_mod._scheduler.get_job("world-cup-refresh") is None
        assert main_mod._scheduler.get_job("world-cup-snapshot-lock") is not None


def test_scheduler_adds_refresh_job_when_enabled(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "initialize_database", lambda: None)
    monkeypatch.setattr(main_mod.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(main_mod.settings, "enable_scheduled_refresh", True)

    app = main_mod.create_app(start_background=True)

    with TestClient(app):
        assert main_mod._scheduler is not None
        assert main_mod._scheduler.get_job("world-cup-refresh") is not None


def test_scheduler_adds_auto_ai_job_when_auto_mode_enabled(monkeypatch):
    import app.main as main_mod

    monkeypatch.setattr(main_mod, "initialize_database", lambda: None)
    monkeypatch.setattr(main_mod.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(main_mod.settings, "enable_ai_prediction", True)
    monkeypatch.setattr(main_mod.settings, "ai_run_mode", "auto")

    app = main_mod.create_app(start_background=True)

    with TestClient(app):
        assert main_mod._scheduler is not None
        assert main_mod._scheduler.get_job("world-cup-auto-ai") is not None


def test_scheduled_auto_ai_dispatches_pre_match_workflow(monkeypatch):
    import app.main as main_mod
    from app.workflows import service as workflow_service

    monkeypatch.setattr(main_mod, "initialize_database", lambda: None)
    monkeypatch.setattr(main_mod.BackgroundScheduler, "start", lambda self: None)
    monkeypatch.setattr(main_mod.settings, "enable_ai_prediction", True)
    monkeypatch.setattr(main_mod.settings, "ai_run_mode", "auto")

    dispatched: list[tuple] = []

    monkeypatch.setattr(
        workflow_service,
        "get_workflow_status",
        lambda: {"button_states": {"ai_prediction": {"enabled": True, "reason": "可运行"}}},
    )
    monkeypatch.setattr(
        workflow_service,
        "run_pre_match_workflow",
        lambda **kwargs: dispatched.append((kwargs["trigger_source"], kwargs["with_ai"], kwargs["with_ensemble"])) or 7,
    )

    app = main_mod.create_app(start_background=True)

    with TestClient(app):
        assert main_mod._scheduler is not None
        job = main_mod._scheduler.get_job("world-cup-auto-ai")
        assert job is not None
        job.func()

    assert dispatched == [("auto_scheduler", True, True)]
