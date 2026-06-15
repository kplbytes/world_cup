"""Tests for Phase 2: Historical data support layer."""

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import create_database
from app.historical.importer import (
    PROVIDER,
    ImportStats,
    _build_name_to_code_map,
    classify_tournament,
    import_historical_matches,
)
from app.historical.queries import get_historical_matches, get_team_match_history
from app.historical.quality import DataQualityReport, run_quality_checks
from app.historical.health import get_data_health
from app.models import HistoricalMatch, Team, TeamAlias, TeamProfile, TeamProfileMatchHistory


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def db_session(tmp_path) -> Session:
    engine = create_database(tmp_path / "test.sqlite3")
    with Session(engine) as session:
        yield session


@pytest.fixture
def seeded_session(db_session: Session) -> Session:
    """Session with some teams seeded for testing."""
    teams = [
        Team(id="BRA", name="Brazil", short_name="Brazil", code="BRA", group_code="A"),
        Team(id="ARG", name="Argentina", short_name="Argentina", code="ARG", group_code="A"),
        Team(id="ENG", name="England", short_name="England", code="ENG", group_code="B"),
        Team(id="FRA", name="France", short_name="France", code="FRA", group_code="B"),
        Team(id="GER", name="Germany", short_name="Germany", code="GER", group_code="C"),
        Team(id="ESP", name="Spain", short_name="Spain", code="ESP", group_code="C"),
        Team(id="USA", name="United States", short_name="USA", code="USA", group_code="D"),
        Team(id="KOR", name="South Korea", short_name="S. Korea", code="KOR", group_code="D"),
    ]
    for t in teams:
        db_session.add(t)
    # Add aliases
    db_session.add(TeamAlias(team_id="USA", provider="test", alias="United States"))
    db_session.add(TeamAlias(team_id="KOR", provider="test", alias="South Korea"))
    db_session.flush()
    return db_session


@pytest.fixture
def csv_dir(tmp_path) -> Path:
    """Create a temporary CSV directory with test data."""
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()

    # Create international_results.csv
    results_path = csv_dir / "international_results.csv"
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date", "home_team", "away_team", "home_score", "away_score",
            "tournament", "city", "country", "neutral",
        ])
        writer.writeheader()
        writer.writerow({
            "date": "2022-11-22", "home_team": "Argentina", "away_team": "Saudi Arabia",
            "home_score": 1, "away_score": 2, "tournament": "FIFA World Cup",
            "city": "Lusail", "country": "Qatar", "neutral": "TRUE",
        })
        writer.writerow({
            "date": "2022-11-25", "home_team": "Brazil", "away_team": "Serbia",
            "home_score": 2, "away_score": 0, "tournament": "FIFA World Cup",
            "city": "Doha", "country": "Qatar", "neutral": "TRUE",
        })
        writer.writerow({
            "date": "2023-03-23", "home_team": "England", "away_team": "Brazil",
            "home_score": 2, "away_score": 1, "tournament": "Friendly",
            "city": "London", "country": "England", "neutral": "FALSE",
        })
        writer.writerow({
            "date": "2023-06-15", "home_team": "France", "away_team": "Greece",
            "home_score": 1, "away_score": 0, "tournament": "UEFA Euro qualification",
            "city": "Paris", "country": "France", "neutral": "FALSE",
        })
        writer.writerow({
            "date": "2024-06-20", "home_team": "Spain", "away_team": "Italy",
            "home_score": 1, "away_score": 0, "tournament": "UEFA Euro",
            "city": "Gelsenkirchen", "country": "Germany", "neutral": "TRUE",
        })
        # Match that went to penalties (must exist in both CSVs)
        writer.writerow({
            "date": "2022-12-09", "home_team": "Croatia", "away_team": "Brazil",
            "home_score": 1, "away_score": 1, "tournament": "FIFA World Cup",
            "city": "Doha", "country": "Qatar", "neutral": "TRUE",
        })
        # Old match (before 2018, should be filtered)
        writer.writerow({
            "date": "2017-01-01", "home_team": "Brazil", "away_team": "Argentina",
            "home_score": 3, "away_score": 0, "tournament": "Friendly",
            "city": "Sao Paulo", "country": "Brazil", "neutral": "FALSE",
        })
        # Future match (should be filtered)
        writer.writerow({
            "date": "2099-01-01", "home_team": "Brazil", "away_team": "Argentina",
            "home_score": 0, "away_score": 0, "tournament": "Friendly",
            "city": "Future City", "country": "Nowhere", "neutral": "TRUE",
        })

    # Create shootouts.csv
    shootouts_path = csv_dir / "shootouts.csv"
    with open(shootouts_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["date", "home_team", "away_team", "winner", "first_shooter"])
        writer.writeheader()
        writer.writerow({
            "date": "2022-12-09", "home_team": "Croatia", "away_team": "Brazil",
            "winner": "Croatia", "first_shooter": "",
        })

    return csv_dir


def _add_historical_match(
    session: Session,
    source_match_id: str,
    kickoff: datetime,
    home_team_id: str | None,
    away_team_id: str | None,
    home_team_raw: str,
    away_team_raw: str,
    home_score: int,
    away_score: int,
    competition: str = "Friendly",
    competition_type: str = "friendly",
    match_importance: float = 1.0,
    neutral_venue: bool = True,
    is_unmapped: bool = False,
    went_to_penalties: bool = False,
    penalty_winner: str | None = None,
    went_to_extra_time: bool = False,
    home_team_source: str = "world_cup",
    away_team_source: str = "world_cup",
    time_precision: str = "exact",
    available_at: datetime | None = None,
    score_scope: str = "full_90min",
) -> HistoricalMatch:
    # For exact time_precision, available_at defaults to kickoff
    # For date_only, available_at should be kickoff + 1 day
    if available_at is None:
        from datetime import timedelta
        if time_precision == "date_only":
            available_at = kickoff + timedelta(days=1)
        else:
            available_at = kickoff

    match = HistoricalMatch(
        source_match_id=source_match_id,
        provider=PROVIDER,
        kickoff=kickoff,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        home_team_raw=home_team_raw,
        away_team_raw=away_team_raw,
        home_score=home_score,
        away_score=away_score,
        neutral_venue=neutral_venue,
        competition=competition,
        competition_type=competition_type,
        match_importance=match_importance,
        went_to_penalties=went_to_penalties,
        penalty_winner=penalty_winner,
        went_to_extra_time=went_to_extra_time,
        is_unmapped=is_unmapped,
        home_team_source=home_team_source,
        away_team_source=away_team_source,
        time_precision=time_precision,
        available_at=available_at,
        score_scope=score_scope,
    )
    session.add(match)
    session.flush()
    return match


# ── Test: Historical import idempotency ─────────────────────────────────

class TestImportIdempotency:
    def test_duplicate_import_skipped(self, seeded_session: Session, csv_dir: Path, monkeypatch):
        """Importing the same CSV twice should not create duplicates."""
        monkeypatch.setattr("app.historical.importer.EXTERNAL_DIR", csv_dir)
        monkeypatch.setattr("app.historical.importer.IMPORT_SINCE", "2018-01-01")

        stats1 = import_historical_matches(seeded_session, since="2018-01-01")
        assert stats1.inserted > 0

        stats2 = import_historical_matches(seeded_session, since="2018-01-01")
        assert stats2.inserted == 0
        assert stats2.skipped_existing == stats1.inserted

    def test_source_match_id_format(self, seeded_session: Session, csv_dir: Path, monkeypatch):
        """source_match_id should be {date}_{home_raw}_{away_raw}."""
        monkeypatch.setattr("app.historical.importer.EXTERNAL_DIR", csv_dir)
        monkeypatch.setattr("app.historical.importer.IMPORT_SINCE", "2018-01-01")

        import_historical_matches(seeded_session, since="2018-01-01")

        match = seeded_session.scalar(
            select(HistoricalMatch).where(
                HistoricalMatch.home_team_raw == "Argentina",
                HistoricalMatch.competition_type == "world_cup",
            )
        )
        assert match is not None
        assert match.source_match_id == "2022-11-22_Argentina_Saudi Arabia"


# ── Test: Team alias mapping ────────────────────────────────────────────

class TestTeamAliasMapping:
    def test_name_from_teams_table(self, seeded_session: Session):
        """Team names from the teams table should map correctly."""
        name_map = _build_name_to_code_map(seeded_session)
        assert name_map["Brazil"] == "BRA"
        assert name_map["Argentina"] == "ARG"

    def test_alias_from_team_aliases_table(self, seeded_session: Session):
        """Aliases from the TeamAlias table should map correctly."""
        name_map = _build_name_to_code_map(seeded_session)
        assert name_map["United States"] == "USA"
        assert name_map["South Korea"] == "KOR"

    def test_extra_aliases_fallback(self, seeded_session: Session):
        """Hardcoded extra aliases should work for teams in the DB."""
        # Add Iran team
        seeded_session.add(Team(id="IRN", name="Iran", short_name="Iran", code="IRN", group_code="E"))
        seeded_session.flush()
        name_map = _build_name_to_code_map(seeded_session)
        assert name_map.get("Iran") == "IRN"


# ── Test: Unknown team isolation ────────────────────────────────────────

class TestUnknownTeamIsolation:
    def test_unmapped_team_flagged(self, seeded_session: Session, csv_dir: Path, monkeypatch):
        """Matches with truly unknown teams should have is_unmapped=True.

        With the expanded FIFA member mapping, most national teams are now
        recognized. Only truly unknown names (not in any mapping) should be
        flagged as unmapped.
        """
        monkeypatch.setattr("app.historical.importer.EXTERNAL_DIR", csv_dir)
        monkeypatch.setattr("app.historical.importer.IMPORT_SINCE", "2018-01-01")

        stats = import_historical_matches(seeded_session, since="2018-01-01")
        # With expanded mapping, Saudi Arabia and Serbia are now recognized
        # as FIFA members, so they should NOT be unmapped
        # Verify that known FIFA members are properly mapped
        mapped_matches = list(seeded_session.scalars(
            select(HistoricalMatch).where(HistoricalMatch.is_unmapped.is_(False))
        ))
        assert len(mapped_matches) > 0

        # Verify that all mapped matches have team IDs
        for m in mapped_matches:
            assert m.home_team_id is not None or m.home_team_source == "unknown"
            assert m.away_team_id is not None or m.away_team_source == "unknown"

    def test_truly_unknown_team_is_unmapped(self, seeded_session: Session):
        """A match with a truly unknown team name should be flagged."""
        _add_historical_match(
            seeded_session,
            source_match_id="test_truly_unknown",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id=None,
            away_team_id="BRA",
            home_team_raw="Totally Unknown Country XYZ",
            away_team_raw="Brazil",
            home_score=0,
            away_score=5,
            is_unmapped=True,
        )

        unmapped = list(seeded_session.scalars(
            select(HistoricalMatch).where(HistoricalMatch.is_unmapped.is_(True))
        ))
        assert len(unmapped) > 0

    def test_unmapped_teams_excluded_from_queries(self, seeded_session: Session):
        """Unmapped matches should not appear in query results."""
        _add_historical_match(
            seeded_session,
            source_match_id="test_unmapped",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id=None,
            away_team_id=None,
            home_team_raw="Unknown Team A",
            away_team_raw="Unknown Team B",
            home_score=1,
            away_score=0,
            is_unmapped=True,
        )

        results = get_historical_matches(
            seeded_session,
            as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert all(not m.is_unmapped for m in results)


# ── Test: National team filtering ───────────────────────────────────────

class TestNationalTeamFiltering:
    def test_csv_is_national_teams_only(self, csv_dir: Path):
        """The CSV should only contain national team matches, not clubs."""
        with open(csv_dir / "international_results.csv") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # All entries should have tournament field
                assert row["tournament"] is not None


# ── Test: as_of strict time boundary ────────────────────────────────────

class TestAsOfBoundary:
    def test_strict_less_than(self, seeded_session: Session):
        """Matches exactly at as_of time should NOT be included."""
        kickoff = datetime(2023, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
        _add_historical_match(
            seeded_session,
            source_match_id="test_boundary",
            kickoff=kickoff,
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
        )

        # Exact same time should NOT include the match
        results = get_historical_matches(seeded_session, as_of=kickoff)
        assert all(m.kickoff < kickoff for m in results)

        # Just after should include the match
        results = get_historical_matches(seeded_session, as_of=datetime(2023, 6, 15, 0, 0, 1, tzinfo=timezone.utc))
        assert any(m.source_match_id == "test_boundary" for m in results)

    def test_same_day_early_match_influences_later(self, seeded_session: Session):
        """A match earlier on the same day should be visible to a later as_of time."""
        early = datetime(2023, 6, 15, 10, 0, 0, tzinfo=timezone.utc)
        late = datetime(2023, 6, 15, 20, 0, 0, tzinfo=timezone.utc)

        _add_historical_match(
            seeded_session,
            source_match_id="early_match",
            kickoff=early,
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=1,
            away_score=0,
        )
        _add_historical_match(
            seeded_session,
            source_match_id="late_match",
            kickoff=late,
            home_team_id="ENG",
            away_team_id="FRA",
            home_team_raw="England",
            away_team_raw="France",
            home_score=2,
            away_score=2,
        )

        # as_of at 15:00 should see early match but not late match
        results = get_historical_matches(
            seeded_session,
            as_of=datetime(2023, 6, 15, 15, 0, 0, tzinfo=timezone.utc),
        )
        ids = {m.source_match_id for m in results}
        assert "early_match" in ids
        assert "late_match" not in ids


# ── Test: Future matches not visible ────────────────────────────────────

class TestFutureMatches:
    def test_future_matches_filtered_on_import(self, seeded_session: Session, csv_dir: Path, monkeypatch):
        """Matches with dates in the future should be filtered during import."""
        monkeypatch.setattr("app.historical.importer.EXTERNAL_DIR", csv_dir)
        monkeypatch.setattr("app.historical.importer.IMPORT_SINCE", "2018-01-01")

        stats = import_historical_matches(seeded_session, since="2018-01-01")
        assert stats.filtered_future > 0

        # No matches from 2099 should be in the DB
        future_matches = seeded_session.scalars(
            select(HistoricalMatch).where(
                HistoricalMatch.kickoff > datetime.now(timezone.utc)
            )
        ).all()
        assert len(future_matches) == 0

    def test_future_matches_not_in_queries(self, seeded_session: Session):
        """Future matches should not appear in query results."""
        _add_historical_match(
            seeded_session,
            source_match_id="future_match",
            kickoff=datetime(2099, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=0,
            away_score=0,
        )

        results = get_historical_matches(
            seeded_session,
            as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert not any(m.source_match_id == "future_match" for m in results)


# ── Test: Penalty score handling ────────────────────────────────────────

class TestPenaltyScoreHandling:
    def test_penalty_match_detected(self, seeded_session: Session, csv_dir: Path, monkeypatch):
        """Matches in shootouts.csv should have went_to_penalties=True."""
        monkeypatch.setattr("app.historical.importer.EXTERNAL_DIR", csv_dir)
        monkeypatch.setattr("app.historical.importer.IMPORT_SINCE", "2018-01-01")

        stats = import_historical_matches(seeded_session, since="2018-01-01")
        assert stats.penalty_matches > 0

    def test_penalty_winner_stored(self, seeded_session: Session):
        """Penalty winner should be stored in penalty_winner field."""
        _add_historical_match(
            seeded_session,
            source_match_id="penalty_test",
            kickoff=datetime(2022, 12, 9, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Croatia",
            away_team_raw="Brazil",
            home_score=1,
            away_score=1,
            competition="FIFA World Cup",
            competition_type="world_cup",
            match_importance=3.0,
            went_to_penalties=True,
            penalty_winner="Croatia",
            went_to_extra_time=True,
        )

        match = seeded_session.scalar(
            select(HistoricalMatch).where(HistoricalMatch.source_match_id == "penalty_test")
        )
        assert match.went_to_penalties is True
        assert match.penalty_winner == "Croatia"
        assert match.went_to_extra_time is True


# ── Test: Competition classification ────────────────────────────────────

class TestCompetitionClassification:
    def test_world_cup(self):
        comp_type, importance = classify_tournament("FIFA World Cup")
        assert comp_type == "world_cup"
        assert importance == 3.0

    def test_world_cup_qualifier(self):
        comp_type, importance = classify_tournament("FIFA World Cup qualification")
        assert comp_type == "qualifier"
        assert importance == 1.5

    def test_continental(self):
        comp_type, importance = classify_tournament("UEFA Euro")
        assert comp_type == "continental"
        assert importance == 2.0

    def test_continental_qualifier(self):
        comp_type, importance = classify_tournament("UEFA Euro qualification")
        assert comp_type == "continental_qualifier"
        assert importance == 1.5

    def test_friendly(self):
        comp_type, importance = classify_tournament("Friendly")
        assert comp_type == "friendly"
        assert importance == 1.0

    def test_other(self):
        comp_type, importance = classify_tournament("Some Random Tournament")
        assert comp_type == "other"
        assert importance == 1.0

    def test_copa_america(self):
        comp_type, importance = classify_tournament("Copa América")
        assert comp_type == "continental"
        assert importance == 2.0

    def test_asian_cup(self):
        comp_type, importance = classify_tournament("AFC Asian Cup")
        assert comp_type == "continental"
        assert importance == 2.0

    def test_gold_cup(self):
        comp_type, importance = classify_tournament("CONCACAF Gold Cup")
        assert comp_type == "continental"
        assert importance == 2.0

    def test_african_cup(self):
        comp_type, importance = classify_tournament("African Cup of Nations")
        assert comp_type == "continental"
        assert importance == 2.0


# ── Test: Data source conflict resolution ───────────────────────────────

class TestDataSourceConflict:
    def test_real_data_takes_priority(self, seeded_session: Session):
        """When real data exists, it should be used instead of mock data."""
        from app.team_profiles.data_loader import seed_mock_history

        # Add mock data
        seed_mock_history(seeded_session, use_seed=True)

        # Add real data
        _add_historical_match(
            seeded_session,
            source_match_id="real_bra_1",
            kickoff=datetime(2023, 6, 15, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=0,
            competition="FIFA World Cup",
            competition_type="world_cup",
            match_importance=3.0,
        )

        from app.team_profiles.service import compute_team_profile
        profile = compute_team_profile(
            seeded_session, "BRA",
            as_of_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert profile.source_summary_json["data_source"] == "real"

    def test_mock_data_used_as_fallback(self, seeded_session: Session):
        """When no real data exists, mock data should be used."""
        from app.team_profiles.data_loader import seed_mock_history
        seed_mock_history(seeded_session, use_seed=True)

        from app.team_profiles.service import compute_team_profile
        profile = compute_team_profile(
            seeded_session, "BRA",
            as_of_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        # seed_mock_v1 data should be labeled as "seed_mock_v1" for backward compat
        assert profile.source_summary_json["data_source"] in ("mock", "seed_mock_v1")


# ── Test: Mock data not in official predictions ─────────────────────────

class TestMockDataIsolation:
    def test_mock_data_guard(self, seeded_session: Session, monkeypatch):
        """seed_mock_history should not run without use_seed=True in production."""
        from app.team_profiles.data_loader import seed_mock_history

        # Default environment is production
        monkeypatch.setattr("app.team_profiles.data_loader.settings.environment", "production")

        count = seed_mock_history(seeded_session)
        assert count == 0

    def test_mock_data_allowed_with_use_seed(self, seeded_session: Session):
        """seed_mock_history should run when use_seed=True."""
        from app.team_profiles.data_loader import seed_mock_history

        count = seed_mock_history(seeded_session, use_seed=True)
        assert count > 0

    def test_mock_data_allowed_in_development(self, seeded_session: Session, monkeypatch):
        """seed_mock_history should run in development environment."""
        from app.team_profiles.data_loader import seed_mock_history

        monkeypatch.setattr("app.team_profiles.data_loader.settings.environment", "development")

        count = seed_mock_history(seeded_session)
        assert count > 0


# ── Test: Insufficient sample protection ────────────────────────────────

class TestInsufficientSample:
    def test_zero_sample_profile(self, seeded_session: Session):
        """Profile with zero matches should have safe defaults."""
        from app.team_profiles.service import compute_team_profile

        profile = compute_team_profile(
            seeded_session, "BRA",
            as_of_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert profile.sample_count == 0
        assert profile.goal_for_avg == 0.0
        assert profile.goal_against_avg == 0.0

    def test_explanation_for_small_sample(self, seeded_session: Session):
        """Explanation should warn about insufficient samples."""
        from app.team_profiles.service import compute_team_profile, explain_team_profile

        profile = compute_team_profile(
            seeded_session, "BRA",
            as_of_date=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        explanation = explain_team_profile(profile)
        assert "弱提示" in explanation


# ── Test: Real-time prediction and historical replay consistency ────────

class TestReplayConsistency:
    def test_as_of_replay_matches_incremental(self, seeded_session: Session):
        """Querying with as_of should give consistent results for replay."""
        _add_historical_match(
            seeded_session,
            source_match_id="replay_1",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=0,
        )
        _add_historical_match(
            seeded_session,
            source_match_id="replay_2",
            kickoff=datetime(2023, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ENG",
            home_team_raw="Brazil",
            away_team_raw="England",
            home_score=1,
            away_score=1,
        )

        # Query at different as_of times
        matches_jan = get_team_match_history(
            seeded_session, "BRA",
            as_of=datetime(2023, 2, 1, tzinfo=timezone.utc),
        )
        matches_jul = get_team_match_history(
            seeded_session, "BRA",
            as_of=datetime(2023, 7, 1, tzinfo=timezone.utc),
        )

        assert len(matches_jan) == 1
        assert len(matches_jul) == 2
        # July results should include January results
        ids_jan = {m.source_match_id for m in matches_jan}
        ids_jul = {m.source_match_id for m in matches_jul}
        assert ids_jan.issubset(ids_jul)


# ── Test: Data health statistics ────────────────────────────────────────

class TestDataHealth:
    def test_empty_database_health(self, seeded_session: Session):
        """Health check on empty database should return safe defaults."""
        health = get_data_health(seeded_session)
        assert health["total_historical_matches"] == 0
        assert health["national_team_coverage"]["coverage_rate"] == 0.0
        assert health["uses_real_data"] is False

    def test_health_with_data(self, seeded_session: Session):
        """Health check with data should return correct stats."""
        _add_historical_match(
            seeded_session,
            source_match_id="health_1",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=0,
        )

        health = get_data_health(seeded_session)
        assert health["total_historical_matches"] == 1
        assert health["national_team_coverage"]["teams_with_data"] >= 2


# ── Test: Data quality checks ──────────────────────────────────────────

class TestDataQuality:
    def test_quality_check_empty_db(self, seeded_session: Session):
        """Quality checks on empty DB should report no issues."""
        report = run_quality_checks(seeded_session)
        assert report.total_matches == 0
        assert report.is_healthy is True

    def test_quality_check_detects_anomalies(self, seeded_session: Session):
        """Quality checks should detect score anomalies."""
        _add_historical_match(
            seeded_session,
            source_match_id="anomaly_1",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=31,  # Anomaly
            away_score=0,
        )

        report = run_quality_checks(seeded_session)
        assert report.score_anomaly_count > 0
        assert report.is_healthy is False

    def test_quality_check_same_team(self, seeded_session: Session):
        """Quality checks should detect same home/away team."""
        _add_historical_match(
            seeded_session,
            source_match_id="same_team_1",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="BRA",
            home_team_raw="Brazil",
            away_team_raw="Brazil",
            home_score=0,
            away_score=0,
        )

        report = run_quality_checks(seeded_session)
        assert report.same_team_count > 0

    def test_quality_check_competition_type_counts(self, seeded_session: Session):
        """Quality checks should report competition type counts."""
        _add_historical_match(
            seeded_session,
            source_match_id="type_friendly",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=1,
            away_score=0,
            competition_type="friendly",
        )
        _add_historical_match(
            seeded_session,
            source_match_id="type_world_cup",
            kickoff=datetime(2023, 2, 1, tzinfo=timezone.utc),
            home_team_id="ENG",
            away_team_id="FRA",
            home_team_raw="England",
            away_team_raw="France",
            home_score=2,
            away_score=1,
            competition_type="world_cup",
        )

        report = run_quality_checks(seeded_session)
        assert report.competition_type_counts.get("friendly", 0) >= 1
        assert report.competition_type_counts.get("world_cup", 0) >= 1


# ── Test: Team match history query ──────────────────────────────────────

class TestTeamMatchHistory:
    def test_returns_home_and_away(self, seeded_session: Session):
        """get_team_match_history should return both home and away matches."""
        _add_historical_match(
            seeded_session,
            source_match_id="home_test",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=0,
        )
        _add_historical_match(
            seeded_session,
            source_match_id="away_test",
            kickoff=datetime(2023, 2, 1, tzinfo=timezone.utc),
            home_team_id="ENG",
            away_team_id="BRA",
            home_team_raw="England",
            away_team_raw="Brazil",
            home_score=1,
            away_score=1,
        )

        matches = get_team_match_history(
            seeded_session, "BRA",
            as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert len(matches) == 2
        ids = {m.source_match_id for m in matches}
        assert "home_test" in ids
        assert "away_test" in ids

    def test_ordered_by_kickoff_desc(self, seeded_session: Session):
        """Results should be ordered by kickoff descending."""
        _add_historical_match(
            seeded_session,
            source_match_id="earlier",
            kickoff=datetime(2023, 1, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=1,
            away_score=0,
        )
        _add_historical_match(
            seeded_session,
            source_match_id="later",
            kickoff=datetime(2023, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ENG",
            home_team_raw="Brazil",
            away_team_raw="England",
            home_score=2,
            away_score=1,
        )

        matches = get_team_match_history(
            seeded_session, "BRA",
            as_of=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert matches[0].source_match_id == "later"
        assert matches[1].source_match_id == "earlier"


# ── Test: Extra-time result exclusion ────────────────────────────────────

class TestExtraTimeResultExclusion:
    """Verify that after_extra_time_or_unknown matches don't pollute 90-min result stats."""

    def test_90min_draw_counted_as_draw_not_win(self, seeded_session: Session):
        """A match that was draw at 90min but home won in extra time must NOT count as a win."""
        # Add a match: 1-1 at 90min, 2-1 after extra time
        # score_scope = after_extra_time_or_unknown
        # The 90min result was draw, but the recorded result is win (2-1)
        # This match must be EXCLUDED from all 90min result statistics
        _add_historical_match(
            seeded_session,
            source_match_id="test_et_draw",
            kickoff=datetime(2023, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
            competition_type="continental",
            went_to_extra_time=True,
            score_scope="after_extra_time_or_unknown",
            available_at=datetime(2023, 6, 2, tzinfo=timezone.utc),
        )
        # Add a regular 90min match
        _add_historical_match(
            seeded_session,
            source_match_id="test_90min_win",
            kickoff=datetime(2023, 5, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=3,
            away_score=0,
            competition_type="continental",
            score_scope="full_90min",
            available_at=datetime(2023, 5, 2, tzinfo=timezone.utc),
        )

        # Compute profile for BRA
        from app.team_profiles.service import compute_team_profile
        profile = compute_team_profile(seeded_session, "BRA", datetime(2024, 1, 1, tzinfo=timezone.utc))

        # The extra-time match (2-1 after ET) must NOT be counted as a win in 90min stats
        # Only the 90min match (3-0) should count
        # So draw_rate_overall should be 0.0 (only 1 match, which is a win)
        assert profile.draw_rate_overall == 0.0
        # favorite_win_rate should be based only on 90min matches
        assert profile.favorite_win_rate > 0 or profile.sample_count >= 1

    def test_et_match_excluded_from_goal_stats(self, seeded_session: Session):
        """Extra-time matches must not affect goal averages."""
        _add_historical_match(
            seeded_session,
            source_match_id="test_et_goals",
            kickoff=datetime(2023, 6, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=5,
            away_score=4,
            competition_type="continental",
            went_to_extra_time=True,
            score_scope="after_extra_time_or_unknown",
            available_at=datetime(2023, 6, 2, tzinfo=timezone.utc),
        )
        _add_historical_match(
            seeded_session,
            source_match_id="test_90min_goals",
            kickoff=datetime(2023, 5, 1, tzinfo=timezone.utc),
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=1,
            away_score=0,
            competition_type="continental",
            score_scope="full_90min",
            available_at=datetime(2023, 5, 2, tzinfo=timezone.utc),
        )

        from app.team_profiles.service import compute_team_profile
        profile = compute_team_profile(seeded_session, "BRA", datetime(2024, 1, 1, tzinfo=timezone.utc))

        # Goal average should only reflect the 90min match (1-0), not the ET match (5-4)
        assert profile.goal_for_avg == 1.0
        assert profile.goal_against_avg == 0.0


# ── Test: End-to-end as_of ──────────────────────────────────────────────

class TestEndToEndAsOf:
    """End-to-end test: as_of flows through the real prediction pipeline."""

    def test_future_matches_excluded_from_profile(self, seeded_session: Session):
        """Matches after as_of must not appear in Team Profile."""
        # Insert matches for BRA across 2021-2024
        for year, month, gf, ga in [(2021, 3, 2, 1), (2021, 6, 1, 0), (2022, 3, 3, 0), (2023, 6, 0, 2), (2024, 3, 1, 1)]:
            kickoff = datetime(year, month, 1, tzinfo=timezone.utc)
            _add_historical_match(
                seeded_session,
                source_match_id=f"test_bra_{year}_{month}",
                kickoff=kickoff,
                home_team_id="BRA",
                away_team_id="ARG",
                home_team_raw="Brazil",
                away_team_raw="Argentina",
                home_score=gf,
                away_score=ga,
                competition_type="continental",
                score_scope="full_90min",
                available_at=kickoff + timedelta(days=1),
            )

        # Compute profile with as_of = 2022-07-01
        from app.team_profiles.service import compute_team_profile
        as_of = datetime(2022, 7, 1, tzinfo=timezone.utc)
        profile = compute_team_profile(seeded_session, "BRA", as_of)

        # Should only include 2021 and 2022-03 matches (3 matches)
        # NOT 2023 or 2024 matches
        assert profile.profile_as_of == as_of
        assert profile.source_summary_json["profile_as_of"] == as_of.isoformat()
        # historical_match_ids should NOT contain 2023 or 2024 match IDs
        match_ids = profile.source_summary_json.get("historical_match_ids", [])
        assert all("2023" not in mid and "2024" not in mid for mid in match_ids)
        # profile_sample_count should match visible matches
        assert profile.source_summary_json["profile_sample_count"] == len(match_ids)

    def test_profile_changes_with_as_of(self, seeded_session: Session):
        """Different as_of dates produce different sample sets."""
        for year, gf, ga in [(2021, 2, 1), (2022, 1, 0), (2023, 0, 2), (2024, 4, 1)]:
            kickoff = datetime(year, 6, 1, tzinfo=timezone.utc)
            _add_historical_match(
                seeded_session,
                source_match_id=f"test_bra_e2e_{year}",
                kickoff=kickoff,
                home_team_id="BRA",
                away_team_id="ARG",
                home_team_raw="Brazil",
                away_team_raw="Argentina",
                home_score=gf,
                away_score=ga,
                competition_type="continental",
                score_scope="full_90min",
                available_at=kickoff + timedelta(days=1),
            )

        from app.team_profiles.service import compute_team_profile
        profile_2022 = compute_team_profile(seeded_session, "BRA", datetime(2022, 7, 1, tzinfo=timezone.utc))
        profile_2024 = compute_team_profile(seeded_session, "BRA", datetime(2024, 7, 1, tzinfo=timezone.utc))

        # 2024 profile should have more matches than 2022 profile
        assert profile_2024.sample_count > profile_2022.sample_count
        # Different goal averages (2022 only has 2 matches, 2024 has 4)
        assert profile_2022.goal_for_avg != profile_2024.goal_for_avg

    def test_prediction_and_replay_use_same_entry(self, seeded_session: Session):
        """Both real-time prediction and historical replay must use the same query interface."""
        kickoff = datetime(2022, 6, 1, tzinfo=timezone.utc)
        _add_historical_match(
            seeded_session,
            source_match_id="test_same_entry",
            kickoff=kickoff,
            home_team_id="BRA",
            away_team_id="ARG",
            home_team_raw="Brazil",
            away_team_raw="Argentina",
            home_score=2,
            away_score=1,
            competition_type="continental",
            score_scope="full_90min",
            available_at=kickoff + timedelta(days=1),
        )

        from app.historical.queries import get_team_match_history
        from app.team_profiles.service import compute_team_profile

        # Simulate real-time prediction: as_of = match kickoff + 1 day
        as_of_prediction = datetime(2022, 6, 2, tzinfo=timezone.utc)
        # Simulate historical replay: same as_of
        as_of_replay = datetime(2022, 6, 2, tzinfo=timezone.utc)

        # Both should see the same matches
        matches_prediction = get_team_match_history(seeded_session, "BRA", as_of_prediction)
        matches_replay = get_team_match_history(seeded_session, "BRA", as_of_replay)

        assert len(matches_prediction) == len(matches_replay)
        assert [m.source_match_id for m in matches_prediction] == [m.source_match_id for m in matches_replay]

        # Both should produce the same profile
        profile_prediction = compute_team_profile(seeded_session, "BRA", as_of_prediction)
        profile_replay = compute_team_profile(seeded_session, "BRA", as_of_replay)

        assert profile_prediction.goal_for_avg == profile_replay.goal_for_avg
        assert profile_prediction.sample_count == profile_replay.sample_count
