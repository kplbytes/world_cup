"""Tests for the unified team matching service."""

import pytest
from sqlalchemy.orm import Session

from app.db import create_database
from app.models import Team, TeamAlias
from app.services.team_matching import (
    resolve_code_alias,
    match_team,
    match_team_id,
    TeamMatchResult,
)


@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = create_database(tmp_path / "test.sqlite3")
    with Session(engine) as session:
        yield session


def _seed_teams(session):
    """Add standard test teams matching canonical IDs from world-cup-2026.json."""
    teams = [
        Team(id="URU", name="Uruguay", short_name="Uruguay", code="URU", group_code="H"),
        Team(id="HAI", name="Haiti", short_name="Haiti", code="HAI", group_code="C"),
        Team(id="IRN", name="IR Iran", short_name="Iran", code="IRN", group_code="G"),
        Team(id="ALG", name="Algeria", short_name="Algeria", code="ALG", group_code="J"),
        Team(id="KOR", name="Korea Republic", short_name="South Korea", code="KOR", group_code="A"),
        Team(id="PRK", name="North Korea", short_name="N.Korea", code="PRK", group_code="F"),
        Team(id="BRA", name="Brazil", short_name="Brazil", code="BRA", group_code="C"),
    ]
    session.add_all(teams)
    session.flush()


class TestResolveCodeAlias:
    def test_ury_resolves_to_urU(self):
        assert resolve_code_alias("URY") == "URU"

    def test_hti_resolves_to_hai(self):
        assert resolve_code_alias("HTI") == "HAI"

    def test_iri_resolves_to_irn(self):
        assert resolve_code_alias("IRI") == "IRN"

    def test_dza_resolves_to_alg(self):
        assert resolve_code_alias("DZA") == "ALG"

    def test_canonical_code_unchanged(self):
        assert resolve_code_alias("URU") == "URU"
        assert resolve_code_alias("BRA") == "BRA"
        assert resolve_code_alias("HAI") == "HAI"

    def test_case_insensitive(self):
        assert resolve_code_alias("ury") == "URU"
        assert resolve_code_alias("Hti") == "HAI"


class TestMatchTeam:
    def test_exact_team_id_match(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "BRA")
        assert result.team_id == "BRA"
        assert result.confidence == "exact"
        assert result.method == "team_id_lookup"

    def test_code_alias_match_ury(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "URY")
        assert result.team_id == "URU"
        assert result.confidence == "alias"
        assert "alias" in result.method.lower() or "resolution" in result.method.lower()

    def test_code_alias_match_hti(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "HTI")
        assert result.team_id == "HAI"
        assert result.confidence == "alias"

    def test_code_alias_match_iri(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "IRI")
        assert result.team_id == "IRN"
        assert result.confidence == "alias"

    def test_code_alias_match_dza(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "DZA")
        assert result.team_id == "ALG"
        assert result.confidence == "alias"

    def test_provider_alias_match(self, db_session):
        _seed_teams(db_session)
        alias = TeamAlias(team_id="KOR", provider="sporttery", alias="韩国")
        db_session.add(alias)
        db_session.flush()

        result = match_team(db_session, "韩国", provider="sporttery")
        assert result.team_id == "KOR"
        assert result.confidence == "provider_alias"

    def test_no_match_returns_none(self, db_session):
        _seed_teams(db_session)
        result = match_team(db_session, "XYZ")
        assert result.team_id is None
        assert result.confidence == "none"

    def test_empty_input_returns_none(self, db_session):
        result = match_team(db_session, "")
        assert result.team_id is None
        assert result.confidence == "none"

    def test_korea_no_false_match(self, db_session):
        """South Korea and North Korea must not be confused."""
        _seed_teams(db_session)
        # "Korea" should NOT match either team via exact or alias
        result = match_team(db_session, "Korea")
        assert result.team_id is None  # No exact match, no alias

    def test_match_team_id_convenience(self, db_session):
        _seed_teams(db_session)
        assert match_team_id(db_session, "BRA") == "BRA"
        assert match_team_id(db_session, "URY") == "URU"
        assert match_team_id(db_session, "XYZ") is None
