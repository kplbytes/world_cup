import pytest
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.intelligence.providers.sportmonks import SportMonksIntelligenceProvider
from app.models import MatchIntelligence, ProviderQuotaState, Team, Match

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

def test_sportmonks_provider_no_token(db_session, monkeypatch):
    monkeypatch.setattr(settings, "sportmonks_token", "")
    provider = SportMonksIntelligenceProvider()
    kickoff = datetime.now(timezone.utc) + timedelta(hours=1)

    ids = provider.fetch_intelligence(db_session, "test_match_sm", kickoff, "TeamA", "TeamB")
    assert ids == []

def test_sportmonks_provider_success(db_session, monkeypatch):
    monkeypatch.setattr(settings, "sportmonks_token", "mock_token")

    db_session.add_all([
        Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"),
        Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="B")
    ])
    db_session.flush()
    db_session.add(Match(id="test_match_sm", group_code="A", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled"))
    db_session.flush()

    provider = SportMonksIntelligenceProvider()
    kickoff = datetime.now(timezone.utc) + timedelta(minutes=45)

    def mock_get(url, *args, **kwargs):
        return MockResponse({
            "data": [
                {
                    "injuries": [
                        {"player_name": "Player 1", "is_home": True, "reason": "Hamstring"},
                        {"player_name": "Player 2", "is_home": False, "reason": "Knee", "type": "Missing"}
                    ],
                    "suspensions": [
                        {"player_name": "Player 3", "is_home": True, "reason": "Red Card"}
                    ]
                }
            ]
        })

    monkeypatch.setattr("httpx.Client.get", mock_get)

    ids = provider.fetch_intelligence(db_session, "test_match_sm", kickoff, "TeamA", "TeamB")

    # 2 injuries + 1 suspension = 3
    assert len(ids) == 3

    records = db_session.query(MatchIntelligence).filter(MatchIntelligence.id.in_(ids)).all()
    assert len(records) == 3

    home_injuries = [r for r in records if r.intelligence_type == "injuries" and r.normalized_payload["affected_team_id"] == "TeamA"]
    assert len(home_injuries) == 1
    assert home_injuries[0].normalized_payload["player_name"] == "Player 1"
    assert home_injuries[0].normalized_payload["reason"] == "Hamstring"

    away_injuries = [r for r in records if r.intelligence_type == "injuries" and r.normalized_payload["affected_team_id"] == "TeamB"]
    assert len(away_injuries) == 1

    home_suspensions = [r for r in records if r.intelligence_type == "suspensions" and r.normalized_payload["affected_team_id"] == "TeamA"]
    assert len(home_suspensions) == 1

def test_sportmonks_provider_exceptions(db_session, monkeypatch):
    monkeypatch.setattr(settings, "sportmonks_token", "mock_token")

    db_session.add_all([
        Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"),
        Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="B")
    ])
    db_session.flush()
    db_session.add(Match(id="test_match_sm2", group_code="A", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled"))
    db_session.flush()

    provider = SportMonksIntelligenceProvider()

    # Test HTTP Error
    def mock_get_429(*args, **kwargs):
        return MockResponse({}, status_code=429)
    monkeypatch.setattr("httpx.Client.get", mock_get_429)
    assert provider.fetch_intelligence(db_session, "test_match_sm2", datetime.now(timezone.utc), "TeamA", "TeamB") == []

    # Test Empty Response
    def mock_get_empty(*args, **kwargs):
        return MockResponse({"data": []})
    monkeypatch.setattr("httpx.Client.get", mock_get_empty)
    assert provider.fetch_intelligence(db_session, "test_match_sm2", datetime.now(timezone.utc), "TeamA", "TeamB") == []

    # Test Invalid JSON
    class BadJsonResponse:
        def json(self):
            import json
            raise json.JSONDecodeError("Expecting value", "", 0)
        def raise_for_status(self):
            pass
    def mock_get_bad_json(*args, **kwargs):
        return BadJsonResponse()
    monkeypatch.setattr("httpx.Client.get", mock_get_bad_json)
    assert provider.fetch_intelligence(db_session, "test_match_sm2", datetime.now(timezone.utc), "TeamA", "TeamB") == []

def test_sportmonks_provider_cache(db_session, monkeypatch):
    monkeypatch.setattr(settings, "sportmonks_token", "mock_token")

    db_session.add_all([
        Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"),
        Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="B")
    ])
    db_session.flush()
    db_session.add(Match(id="test_match_cache", group_code="A", home_team_id="TeamA", away_team_id="TeamB", kickoff=datetime.now(timezone.utc), source="test", status="scheduled"))
    db_session.flush()

    provider = SportMonksIntelligenceProvider()

    # Manually insert a recent MatchIntelligence
    intel = MatchIntelligence(
        match_id="test_match_cache",
        provider="sportmonks",
        source_url="cache_test",
        intelligence_type="injuries",
        raw_payload={},
        normalized_payload={"affected_team_id": "TeamA", "player_name": "Cache Player"},
        source_confidence=0.8,
        fetched_at=datetime.now(timezone.utc)
    )
    db_session.add(intel)
    db_session.flush()

    call_count = 0
    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return MockResponse({"data": []})

    monkeypatch.setattr("httpx.Client.get", mock_get)

    # Should hit cache and return [] without incrementing call_count
    ids = provider.fetch_intelligence(db_session, "test_match_cache", datetime.now(timezone.utc), "TeamA", "TeamB")

    assert ids == []
    assert call_count == 0
