from pathlib import Path

from fastapi.testclient import TestClient

from app.db import create_database, session_scope
from app.main import create_app
from app.providers.openfootball import OpenFootballProvider
from app.services.recompute import recompute_all
from app.services.seed import seed_ratings, seed_tournament


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


def test_health_does_not_expose_api_token(tmp_path, monkeypatch):
    monkeypatch.setenv("FOOTBALL_DATA_API_TOKEN", "top-secret-token")
    client = api_client(tmp_path)

    response = client.get("/api/health")

    assert response.status_code == 200
    assert "top-secret-token" not in response.text


def test_sync_runs_returns_list(tmp_path):
    client = api_client(tmp_path)

    response = client.get("/api/sync-runs")

    assert response.status_code == 200
    assert isinstance(response.json(), list)

