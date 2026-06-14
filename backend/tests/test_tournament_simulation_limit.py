"""Tests for tournament simulation iteration clamping."""

from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app.api.routes.tournament_routes import MAX_SIMULATION_ITERATIONS
from app.db import create_database, session_scope
from app.main import create_app
from app.services.seed import seed_ratings, seed_team_aliases, seed_tournament
from app.providers.openfootball import OpenFootballProvider
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).resolve().parents[2]


def _api_client(tmp_path):
    create_database(tmp_path / "tournament_test.sqlite3")
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
    return TestClient(create_app(start_background=False))


def test_simulate_clamps_iterations_to_max(tmp_path):
    """When iterations exceeds MAX_SIMULATION_ITERATIONS, it is clamped."""
    client = _api_client(tmp_path)

    with patch("app.tournament.simulation.run_tournament_simulation") as mock_sim:
        mock_sim.return_value = []
        response = client.post(
            "/api/tournament/simulate",
            params={"iterations": 999_999_999, "seed": 42},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["iterations_requested"] == 999_999_999
    assert payload["iterations_used"] == MAX_SIMULATION_ITERATIONS
    # Verify the simulation was called with the clamped value
    mock_sim.assert_called_once()
    call_kwargs = mock_sim.call_args.kwargs
    assert call_kwargs["iterations"] == MAX_SIMULATION_ITERATIONS


def test_simulate_uses_iterations_within_limit(tmp_path):
    """When iterations is within the limit, it is used as-is."""
    client = _api_client(tmp_path)

    with patch("app.tournament.simulation.run_tournament_simulation") as mock_sim:
        mock_sim.return_value = []
        response = client.post(
            "/api/tournament/simulate",
            params={"iterations": 5000, "seed": 42},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["iterations_requested"] == 5000
    assert payload["iterations_used"] == 5000


def test_simulate_response_includes_iterations_used(tmp_path):
    """The response must include the iterations_used field."""
    client = _api_client(tmp_path)

    with patch("app.tournament.simulation.run_tournament_simulation") as mock_sim:
        mock_sim.return_value = []
        response = client.post(
            "/api/tournament/simulate",
            params={"iterations": 1000, "seed": 42},
        )

    assert response.status_code == 200
    payload = response.json()
    assert "iterations_used" in payload
    assert "iterations_requested" in payload
    assert payload["iterations_used"] == 1000
