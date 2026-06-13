from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select

from app.models import Match, Team
from app.providers.openfootball import OpenFootballProvider
from app.services.seed import seed_tournament


FIXTURES = Path(__file__).parent / "fixtures"


def load_payload():
    return OpenFootballProvider.from_files(
        matches_path=FIXTURES / "openfootball-worldcup-2026.json",
        teams_path=FIXTURES / "openfootball-worldcup-teams-2026.json",
    ).load()


def test_seed_contains_twelve_groups_forty_eight_teams_and_seventy_two_matches(db_session):
    result = seed_tournament(db_session, load_payload())

    assert result.groups == list("ABCDEFGHIJKL")
    assert result.team_count == 48
    assert result.match_count == 72
    assert db_session.scalar(select(func.count(Team.id))) == 48
    assert db_session.scalar(select(func.count(Match.id))) == 72


def test_seed_is_idempotent(db_session):
    payload = load_payload()

    first = seed_tournament(db_session, payload)
    second = seed_tournament(db_session, payload)

    assert first == second
    assert db_session.scalar(select(func.count(Team.id))) == 48
    assert db_session.scalar(select(func.count(Match.id))) == 72


def test_provider_rejects_a_team_assigned_to_the_wrong_group(tmp_path):
    teams = (FIXTURES / "openfootball-worldcup-teams-2026.json").read_text()
    bad_teams = tmp_path / "teams.json"
    bad_teams.write_text(teams.replace('"group": "A"', '"group": "L"', 1))

    provider = OpenFootballProvider.from_files(
        matches_path=FIXTURES / "openfootball-worldcup-2026.json",
        teams_path=bad_teams,
    )

    with pytest.raises(ValidationError):
        provider.load()

