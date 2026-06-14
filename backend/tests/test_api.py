from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import settings
from app.db import create_database, session_scope
from app.main import create_app
from app.models import Match
from app.providers.worldcup26 import WorldCup26Provider
from app.providers.openfootball import OpenFootballProvider
from app.services.recompute import recompute_all
from app.services.seed import seed_ratings, seed_team_aliases, seed_tournament
from app.services.scoring import save_model_score, score_model, snapshot_prediction


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


def api_client(tmp_path):
    create_database(tmp_path / "api.sqlite3")
    with session_scope() as session:
        seed_tournament(
            session,
            OpenFootballProvider.from_files(
                FIXTURES / "openfootball-worldcup-2026.json",
                FIXTURES / "openfootball-worldcup-teams-2026.json",
            ).load(),
        )
        seed_ratings(session, ROOT / "data/seed/elo-ratings-2026.json")
        seed_team_aliases(session, ROOT / "data/seed/sporttery-team-aliases.json")
        recompute_all(session, iterations=100, seed=7)
    return TestClient(create_app(start_background=False))


def test_dashboard_returns_one_complete_revision(tmp_path):
    client = api_client(tmp_path)

    response = client.get("/api/dashboard")

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["groups"]) == 12
    assert sum(len(group["teams"]) for group in payload["groups"]) == 48
    assert sum(len(group["matches"]) for group in payload["groups"]) == 72
    assert {group["code"] for group in payload["groups"]} == set("ABCDEFGHIJKL")
    assert payload["revision"]["id"] > 0


def test_dashboard_uses_latest_pre_kickoff_snapshot_for_finished_match(tmp_path):
    client = api_client(tmp_path)
    with session_scope() as session:
        from app.models import DashboardRevision, PredictionSnapshot
        from sqlalchemy import select

        match = session.get(Match, "2026-B-QAT-SUI-2026-06-13")
        revision = session.scalar(
            select(DashboardRevision).where(DashboardRevision.active.is_(True))
        )
        match.status = "final"
        match.home_score = 1
        match.away_score = 1
        match.kickoff = datetime(2026, 6, 13, 19, tzinfo=timezone.utc)
        session.add_all([
            PredictionSnapshot(
                match_id=match.id,
                revision_id=revision.id,
                kickoff=match.kickoff,
                snapshotted_at=datetime(2026, 6, 13, 18, tzinfo=timezone.utc),
                home_win=0.2,
                draw=0.3,
                away_win=0.5,
                home_xg=0.8,
                away_xg=1.4,
                scorelines=[],
                score_matrix=[],
                confidence=0.5,
                confidence_label="medium",
                model_inputs={},
                model_version="pre-match-test",
            ),
            PredictionSnapshot(
                match_id=match.id,
                revision_id=revision.id,
                kickoff=match.kickoff,
                snapshotted_at=datetime(2026, 6, 13, 20, tzinfo=timezone.utc),
                home_win=0.3,
                draw=0.4,
                away_win=0.3,
                home_xg=1.0,
                away_xg=1.0,
                scorelines=[],
                score_matrix=[],
                confidence=0.4,
                confidence_label="low",
                model_inputs={},
                model_version="post-match-test",
            ),
        ])

    payload = client.get("/api/dashboard").json()
    match_payload = next(
        match
        for group in payload["groups"]
        for match in group["matches"]
        if match["id"] == "2026-B-QAT-SUI-2026-06-13"
    )

    assert match_payload["snapshot_status"] == {
        "locked": True,
        "locked_at": "2026-06-13T18:00:00",
        "is_fallback": False,
        "participates_in_model_score": True,
        "real_time_only": False,
    }


def test_dashboard_uses_chinese_team_names_everywhere(tmp_path):
    client = api_client(tmp_path)

    group = client.get("/api/groups/A").json()
    mexico = next(team for team in group["teams"] if team["id"] == "MEX")
    mexico_match = next(
        match
        for match in group["matches"]
        if match["status"] != "final"
        and "MEX" in (match["home_team"]["id"], match["away_team"]["id"])
    )

    assert mexico["name"] == "墨西哥"
    assert mexico["short_name"] == "墨西哥"
    assert "墨西哥" in {
        mexico_match["home_team"]["short_name"],
        mexico_match["away_team"]["short_name"],
    }
    assert "墨西哥" in mexico_match["prediction"]["explanation"]


def test_group_match_team_and_source_endpoints(tmp_path):
    client = api_client(tmp_path)
    dashboard = client.get("/api/dashboard").json()
    group = dashboard["groups"][0]
    match_id = group["matches"][0]["id"]
    team_id = group["teams"][0]["id"]

    assert client.get("/api/groups/A").status_code == 200
    assert client.get(f"/api/matches/{match_id}").status_code == 200
    assert client.get(f"/api/teams/{team_id}").status_code == 200
    assert client.get("/api/data-sources").status_code == 200
    assert client.get("/api/groups/Z").status_code == 404
    assert client.get("/api/matches/missing").status_code == 404


def test_build_providers_always_includes_worldcup26(monkeypatch):
    from app.api.routes.dashboard_routes import _build_providers

    monkeypatch.setattr("app.api.routes.dashboard_routes.fd_is_configured", lambda: False)

    providers = _build_providers()

    assert any(isinstance(provider, WorldCup26Provider) for provider in providers)


def test_health_does_not_expose_api_token(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "football_data_api_token", "top-secret-token")
    client = api_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "top-secret-token" not in response.text


def test_sync_runs_returns_list(tmp_path):
    client = api_client(tmp_path)

    response = client.get("/api/sync-runs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_model_score_exposes_model_version_history_and_comparison(tmp_path, monkeypatch):
    client = api_client(tmp_path)
    with session_scope() as session:
        dashboard = client.get("/api/dashboard").json()
        match_id = dashboard["groups"][0]["matches"][2]["id"]
        match = session.get(Match, match_id)

        from app.models import PredictionSnapshot
        from sqlalchemy import select
        from datetime import datetime, timezone

        existing = session.scalar(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id, PredictionSnapshot.revision_id == dashboard["revision"]["id"]))
        if existing:
            existing.is_pre_match_locked = True
        else:
            snap = PredictionSnapshot(
                match_id=match_id,
                revision_id=dashboard["revision"]["id"],
                kickoff=datetime.now(timezone.utc),
                is_pre_match_locked=True,
                home_win=0.5, draw=0.3, away_win=0.2, home_xg=1.0, away_xg=1.0,
                scorelines=[], score_matrix=[],
                confidence=0.8, confidence_label="High",
                model_inputs={}, model_version="elo-poisson-v1"
            )
            session.add(snap)
        session.flush()

        match.status = "final"
        match.home_score = 1
        match.away_score = 0
        first_revision = dashboard["revision"]["id"]
        first_report = score_model(session)
        save_model_score(session, first_report, first_revision)

        monkeypatch.setattr("app.services.recompute.MODEL_VERSION", "elo-poisson-v1.1")
        monkeypatch.setattr("app.prediction.poisson.MODEL_VERSION", "elo-poisson-v1.1")
        second_revision = recompute_all(session, iterations=100, seed=11)

        existing2 = session.scalar(select(PredictionSnapshot).where(PredictionSnapshot.match_id == match_id, PredictionSnapshot.revision_id == second_revision.id))
        if existing2:
            existing2.is_pre_match_locked = True
        else:
            snap2 = PredictionSnapshot(
                match_id=match_id,
                revision_id=second_revision.id,
                kickoff=datetime.now(timezone.utc),
                is_pre_match_locked=True,
                home_win=0.6, draw=0.2, away_win=0.2, home_xg=1.2, away_xg=0.8,
                scorelines=[], score_matrix=[],
                confidence=0.9, confidence_label="High",
                model_inputs={}, model_version="elo-poisson-v1.1"
            )
            session.add(snap2)
        session.flush()

        second_report = score_model(session)
        save_model_score(session, second_report, second_revision.id)

    payload = client.get("/api/model-score").json()

    assert payload["model_version"] == "elo-poisson-v1.1"
    assert len(payload["history"]) == 2
    assert payload["history"][0]["model_version"] == "elo-poisson-v1.1"
    assert {item["model_version"] for item in payload["model_versions"]} == {
        "elo-poisson-v1",
        "elo-poisson-v1.1",
    }
    assert payload["comparison"]["current_version"]["model_version"] == "elo-poisson-v1.1"
    assert payload["comparison"]["previous_version"]["model_version"] == "elo-poisson-v1"


def test_decision_review_contains_the_pre_match_prediction(tmp_path, monkeypatch):
    client = api_client(tmp_path)
    with session_scope() as session:
        dashboard = client.get("/api/dashboard").json()
        match_id = dashboard["groups"][0]["matches"][2]["id"]
        match = session.get(Match, match_id)
        snapshot_prediction(session, match_id)
        match.status = "final"
        match.home_score = 1
        match.away_score = 0
        match.kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.services.dashboard.decision_now",
        lambda: datetime(2026, 6, 13, 8, tzinfo=timezone.utc),
    )
    review = client.get("/api/decision").json()["recent_review"]

    item = next(row for row in review if row["id"] == match_id)
    assert item["prediction"]["home_win"] == item["snapshot"]["home_win"]
    assert item["review"]["bias_explanation"]


def test_decision_exposes_review_summary_metrics(tmp_path, monkeypatch):
    client = api_client(tmp_path)
    with session_scope() as session:
        dashboard = client.get("/api/dashboard").json()
        match_id = dashboard["groups"][0]["matches"][2]["id"]
        match = session.get(Match, match_id)
        snapshot_prediction(session, match_id)
        match.status = "final"
        match.home_score = 1
        match.away_score = 0
        match.kickoff = datetime(2026, 6, 12, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.services.dashboard.decision_now",
        lambda: datetime(2026, 6, 13, 8, tzinfo=timezone.utc),
    )
    payload = client.get("/api/decision").json()

    assert payload["review_summary"]["matches_scored"] == 1
    assert payload["review_summary"]["brier_score"] > 0
    assert payload["review_summary"]["log_loss"] > 0
    assert payload["review_summary"]["outcome_hit_rate"] in (0, 1)


def test_decision_today_uses_shanghai_calendar_day(tmp_path, monkeypatch):
    client = api_client(tmp_path)
    with session_scope() as session:
        match = session.get(Match, "2026-B-QAT-SUI-2026-06-13")
        match.kickoff = datetime(2026, 6, 13, 16, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(
        "app.services.dashboard.decision_now",
        lambda: datetime(2026, 6, 13, 8, tzinfo=timezone.utc),
    )
    today = client.get("/api/decision").json()["today_matches"]

    assert any(row["id"] == "2026-B-QAT-SUI-2026-06-13" for row in today)


def test_manual_adjustment_changes_match_prediction_and_can_be_removed(tmp_path):
    client = api_client(tmp_path)
    dashboard = client.get("/api/dashboard").json()
    match_id = dashboard["groups"][0]["matches"][2]["id"]
    before = client.get(f"/api/matches/{match_id}").json()

    response = client.post(
        "/api/manual-adjustments",
        json={
            "match_id": match_id,
            "adjustment_type": "伤停",
            "affected_team_id": before["home_team"]["id"],
            "attack_delta": -0.20,
            "defense_delta": 0.0,
            "confidence": "medium",
            "note": "主力前锋伤缺，主队进攻下调。",
        },
    )

    assert response.status_code == 200
    created = response.json()
    assert created["adjustment"]["note"] == "主力前锋伤缺，主队进攻下调。"
    assert created["revision_id"] > dashboard["revision"]["id"]

    after = client.get(f"/api/matches/{match_id}").json()
    assert after["manual_adjustments"][0]["adjustment_type"] == "伤停"
    assert after["prediction"]["home_xg"] < before["prediction"]["home_xg"]

    listed = client.get(f"/api/manual-adjustments?match_id={match_id}")
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    removed = client.request(
        "DELETE",
        f"/api/manual-adjustments/{created['adjustment']['id']}",
    )
    assert removed.status_code == 200
    assert removed.json()["revision_id"] > created["revision_id"]

    restored = client.get(f"/api/matches/{match_id}").json()
    assert restored["manual_adjustments"] == []
    assert restored["prediction"]["home_xg"] == before["prediction"]["home_xg"]
