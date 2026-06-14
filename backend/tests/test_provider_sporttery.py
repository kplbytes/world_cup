import pytest
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from app.models import Team, Match, MatchIntelligence, TeamAlias
from app.intelligence.providers.sporttery import SportteryIntelligenceProvider
from sqlalchemy import select

_SHANGHAI = ZoneInfo("Asia/Shanghai")

def test_sporttery_provider_fetch_with_cache(db_session, monkeypatch):
    # Setup test data
    db_session.add(Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"))
    db_session.add(Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="A"))
    db_session.flush()

    kickoff = datetime.now(timezone.utc)
    match = Match(
        id="test_match_1", group_code="A", source="test", home_team_id="TeamA", away_team_id="TeamB",
        kickoff=kickoff, venue="V", status="scheduled"
    )
    db_session.add(match)
    db_session.flush()

    # Use Shanghai-timezone date for match_date so it matches _sporttery_match_date(kickoff)
    shanghai_date = kickoff.astimezone(_SHANGHAI).strftime("%Y-%m-%d")

    # Mock the remote fetch
    fetch_calls = []

    def mock_fetch_odds(self):
        fetch_calls.append(1)
        return [
            {
                "match_num": "1001",
                "home_team": "Team A",
                "away_team": "Team B",
                "match_date": f"{shanghai_date} 20:00:00",
                "had_home": 2.0,
                "had_draw": 3.0,
                "had_away": 4.0
            }
        ]

    from app.services.market import SportteryRemoteProvider
    monkeypatch.setattr(SportteryRemoteProvider, "fetch_odds", mock_fetch_odds)

    provider = SportteryIntelligenceProvider(cache_ttl_minutes=5)

    # First fetch should call remote
    ids1 = provider.fetch_intelligence(db_session, "test_match_1", kickoff, "TeamA", "TeamB")
    assert len(ids1) == 1
    assert len(fetch_calls) == 1

    # Second fetch should use cache
    ids2 = provider.fetch_intelligence(db_session, "test_match_1", kickoff, "TeamA", "TeamB")
    assert len(ids2) == 1
    assert ids2[0] != ids1[0]
    assert len(fetch_calls) == 1

    intel = db_session.scalar(select(MatchIntelligence).where(MatchIntelligence.id == ids1[0]))
    assert intel.intelligence_type == "odds"
    assert intel.provider == "sporttery"
    assert "home" in intel.normalized_payload
    assert intel.source_confidence == 0.9


def test_sporttery_provider_supports_team_alias_rows(db_session, monkeypatch):
    db_session.add(Team(id="AAA", name="Team A", short_name="TA", code="TMA", group_code="A"))
    db_session.add(Team(id="BBB", name="Team B", short_name="TB", code="TMB", group_code="A"))
    db_session.add(TeamAlias(team_id="AAA", provider="sporttery", alias="甲队"))
    db_session.add(TeamAlias(team_id="BBB", provider="sporttery", alias="乙队"))
    db_session.flush()

    kickoff = datetime.now(timezone.utc)
    db_session.add(Match(
        id="alias_match", group_code="A", source="test", home_team_id="AAA", away_team_id="BBB",
        kickoff=kickoff, venue="V", status="scheduled"
    ))
    db_session.flush()

    shanghai_date = kickoff.astimezone(_SHANGHAI).strftime("%Y-%m-%d")

    def mock_fetch_odds(self):
        return [{
            "match_num": "1002",
            "home_team": "甲队",
            "away_team": "乙队",
            "match_date": f"{shanghai_date} 20:00:00",
            "had_home": 2.1,
            "had_draw": 3.1,
            "had_away": 3.9,
        }]

    from app.services.market import SportteryRemoteProvider
    monkeypatch.setattr(SportteryRemoteProvider, "fetch_odds", mock_fetch_odds)

    provider = SportteryIntelligenceProvider(cache_ttl_minutes=5)
    ids = provider.fetch_intelligence(db_session, "alias_match", kickoff, "AAA", "BBB")

    assert len(ids) == 1
