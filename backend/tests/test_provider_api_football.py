import pytest
from datetime import datetime, timedelta, timezone
from app.config import settings
from app.intelligence.providers.api_football import ApiFootballIntelligenceProvider
from app.models import MatchIntelligence, ProviderQuotaState

def test_api_football_provider_no_token(db_session, monkeypatch):
    """If no token, it should short-circuit and not fetch."""
    monkeypatch.setattr(settings, "api_football_token", "")

    provider = ApiFootballIntelligenceProvider()
    kickoff = datetime.now(timezone.utc) + timedelta(hours=1)

    ids = provider.fetch_intelligence(db_session, "test_match_1", kickoff, "TeamA", "TeamB")
    assert ids == []

class MockResponse:
    def __init__(self, json_data, status_code=200):
        self._json_data = json_data
        self.status_code = status_code

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPError(f"HTTP {self.status_code}")

def test_api_football_provider_success(db_session, monkeypatch):
    """Test successful fetch with mock token."""
    monkeypatch.setattr(settings, "api_football_token", "mock_token")

    # Insert dummy teams and match
    from app.models import Team, Match
    home = Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A")
    away = Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="B")
    match = Match(id="test_match_1", group_code="A", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled")
    db_session.add_all([home, away])
    db_session.flush()
    db_session.add(match)
    db_session.flush()

    provider = ApiFootballIntelligenceProvider()
    kickoff = datetime.now(timezone.utc) + timedelta(minutes=45)

    call_count = 0
    def mock_get(self, url, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if "fixtures?date" in url:
            return MockResponse({
                "response": [
                    {
                        "fixture": {"id": 12345, "status": {"short": "NS"}},
                        "teams": {
                            "home": {"name": "Team A"},
                            "away": {"name": "Team B"}
                        }
                    }
                ]
            })
        elif "lineups" in url:
            return MockResponse({
                "response": [
                    {
                        "team": {"name": "Team A"},
                        "formation": "4-3-3",
                        "startXI": [{"player": {"name": "Player 1"}}]
                    },
                    {
                        "team": {"name": "Team B"},
                        "formation": "4-2-3-1",
                        "startXI": [{"player": {"name": "Player 2"}}]
                    }
                ]
            })
        return MockResponse({})

    monkeypatch.setattr("httpx.Client.get", mock_get)

    ids = provider.fetch_intelligence(db_session, "test_match_1", kickoff, "TeamA", "TeamB")

    # Should insert two intelligences: fixtures and lineups
    assert len(ids) == 2

    # Verify records
    records = db_session.query(MatchIntelligence).filter(MatchIntelligence.id.in_(ids)).all()
    assert len(records) == 2

    fixtures = next((r for r in records if r.intelligence_type == "fixtures"), None)
    assert fixtures is not None
    assert fixtures.normalized_payload["fixture_id"] == 12345

    lineups = next((r for r in records if r.intelligence_type == "lineups"), None)
    assert lineups is not None
    assert lineups.normalized_payload["is_official"] is True
    assert lineups.normalized_payload["home_formation"] == "4-3-3"

    # Check quota
    quota = db_session.get(ProviderQuotaState, "api-football")
    assert quota is not None
    assert quota.used_today == 2 # 2 requests made

    # Now if we fetch again, it should use cache!
    call_count_before = call_count
    ids_again = provider.fetch_intelligence(db_session, "test_match_1", kickoff, "TeamA", "TeamB")

    # Should not make any new requests
    assert call_count == call_count_before
    # But it doesn't return new IDs since they are cached
    assert len(ids_again) == 0

def test_api_football_provider_quota_exceeded(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_football_token", "mock_token")
    provider = ApiFootballIntelligenceProvider()

    # Set quota to exhausted
    quota = ProviderQuotaState(provider="api-football", daily_limit=100, used_today=100, reset_at=datetime.now(timezone.utc) + timedelta(hours=2))
    db_session.add(quota)
    db_session.flush()

    # Insert dummy match
    from app.models import Match, Team
    home = Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A")
    away = Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="B")
    match = Match(id="test_match_2", group_code="B", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled")
    db_session.add_all([home, away])
    db_session.flush()
    db_session.add(match)
    db_session.flush()

    ids = provider.fetch_intelligence(db_session, "test_match_2", datetime.now(timezone.utc), "TeamA", "TeamB")
    assert ids == []

def test_api_football_provider_http_error(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_football_token", "mock_token")
    provider = ApiFootballIntelligenceProvider()

    def mock_get(*args, **kwargs):
        return MockResponse({}, status_code=429)
    monkeypatch.setattr("httpx.Client.get", mock_get)

    # Should safely catch the HTTPError and return []
    ids = provider.fetch_intelligence(db_session, "test_match_1", datetime.now(timezone.utc), "TeamA", "TeamB")
    assert ids == []

def test_api_football_provider_empty_response(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_football_token", "mock_token")
    provider = ApiFootballIntelligenceProvider()

    def mock_get(*args, **kwargs):
        return MockResponse({"response": []})
    monkeypatch.setattr("httpx.Client.get", mock_get)

    # Should safely process empty response and return []
    ids = provider.fetch_intelligence(db_session, "test_match_1", datetime.now(timezone.utc), "TeamA", "TeamB")
    assert ids == []

def test_api_football_provider_missing_fields(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_football_token", "mock_token")
    provider = ApiFootballIntelligenceProvider()

    def mock_get(self, url, *args, **kwargs):
        if "fixtures?date" in url:
            # Missing 'fixture' inside the response item
            return MockResponse({"response": [{"teams": {}}]})
        elif "lineups" in url:
            # Missing 'startXI' or 'team'
            return MockResponse({"response": [{"formation": "4-3-3"}]})
        return MockResponse({})
    monkeypatch.setattr("httpx.Client.get", mock_get)

    # KeyError or similar should be caught
    ids = provider.fetch_intelligence(db_session, "test_match_1", datetime.now(timezone.utc), "TeamA", "TeamB")
    assert ids == []

def test_api_football_provider_invalid_json(db_session, monkeypatch):
    monkeypatch.setattr(settings, "api_football_token", "mock_token")
    provider = ApiFootballIntelligenceProvider()

    class BadJsonResponse:
        def json(self):
            import json
            raise json.JSONDecodeError("Expecting value", "", 0)
        def raise_for_status(self):
            pass

    def mock_get(*args, **kwargs):
        return BadJsonResponse()
    monkeypatch.setattr("httpx.Client.get", mock_get)

    # JSONDecodeError should be caught
    ids = provider.fetch_intelligence(db_session, "test_match_1", datetime.now(timezone.utc), "TeamA", "TeamB")
    assert ids == []
