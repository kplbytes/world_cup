from datetime import datetime, timedelta, timezone

from app.main import _is_live_window
from app.models import Match, Team


def _add_match(session, kickoff, status="scheduled"):
    session.add_all(
        [
            Team(id="AAA", name="Alpha", short_name="Alpha", code="AAA", group_code="A"),
            Team(id="BBB", name="Beta", short_name="Beta", code="BBB", group_code="A"),
        ]
    )
    session.flush()
    session.add(
        Match(
            id="match-1",
            group_code="A",
            home_team_id="AAA",
            away_team_id="BBB",
            kickoff=kickoff,
            status=status,
            source="test",
        )
    )
    session.flush()


def test_future_match_does_not_enable_live_refresh(db_session):
    now = datetime(2026, 6, 13, 8, tzinfo=timezone.utc)
    _add_match(db_session, now + timedelta(days=1))

    assert _is_live_window(db_session, now=now) is False


def test_recently_started_match_enables_live_refresh(db_session):
    now = datetime(2026, 6, 13, 8, tzinfo=timezone.utc)
    _add_match(db_session, now - timedelta(hours=1))

    assert _is_live_window(db_session, now=now) is True
