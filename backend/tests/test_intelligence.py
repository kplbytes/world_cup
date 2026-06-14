import pytest
from datetime import datetime, timezone

from sqlalchemy.orm import Session
from app.models import MatchIntelligence, ProviderQuotaState
from app.intelligence.quota import QuotaGuard
from app.intelligence.cache import save_intelligence, get_cached_intelligence

def test_quota_guard_respects_limits(db_session: Session):
    guard = QuotaGuard(db_session, "test_provider", 2)
    assert guard.can_request() is True
    guard.record_request()
    assert guard.can_request() is True
    guard.record_request()
    assert guard.can_request() is False # Reached limit

def test_quota_guard_high_limit_allows_many(db_session: Session):
    guard = QuotaGuard(db_session, "test_provider", 9999)
    assert guard.can_request() is True
    guard.record_request()
    guard.record_request()
    guard.record_request()
    assert guard.can_request() is True

def test_save_intelligence_cache_behavior(db_session: Session):
    match_id = "test_match_cache"

    # Cache should be empty initially
    cached = get_cached_intelligence(db_session, match_id, "mock", "injury", max_age_minutes=10)
    assert cached is None

    from app.models import Team, Match
    from datetime import datetime, timezone
    db_session.add(Team(id="TeamA", name="Team A", short_name="TA", code="TMA", group_code="A"))
    db_session.add(Team(id="TeamB", name="Team B", short_name="TB", code="TMB", group_code="A"))
    db_session.flush()
    db_session.add(Match(
        id=match_id, group_code="A", source="test", home_team_id="TeamA", away_team_id="TeamB",
        kickoff=datetime.now(timezone.utc), venue="V", status="scheduled"
    ))
    db_session.flush()

    # First save
    row1 = save_intelligence(
        db_session, match_id, "mock", "http://mock", "injury",
        {"data": 1}, {"data": 1}, 0.8, "TeamA"
    )
    db_session.flush()

    # Should hit cache now
    cached = get_cached_intelligence(db_session, match_id, "mock", "injury", max_age_minutes=10)
    assert cached is not None
    assert cached == {"data": 1}

    # Should MISS cache if max_age is too short (0 minutes)
    # wait, datetime.now changes so maybe -1 minutes to simulate stale?
    # Actually just max_age_minutes=0
    cached_stale = get_cached_intelligence(db_session, match_id, "mock", "injury", max_age_minutes=-1)
    assert cached_stale is None
