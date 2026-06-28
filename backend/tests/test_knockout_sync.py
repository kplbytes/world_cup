from pathlib import Path

from sqlalchemy import select

from app.models import Match
from app.providers.openfootball import OpenFootballProvider
from app.schemas import TournamentMatch
from app.services.refresh import refresh_tournament
from app.services.seed import seed_ratings, seed_tournament
from app.tournament.knockout import ensure_knockout_placeholders, sync_knockout_state
from app.tournament.standings import get_third_placed_ranking


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"


class StaticProvider:
    def __init__(self, payload):
        self.payload = payload

    def load(self):
        return self.payload


def _seed_base(session):
    payload = OpenFootballProvider.from_files(
        FIXTURES / "openfootball-worldcup-2026.json",
        FIXTURES / "openfootball-worldcup-teams-2026.json",
    ).load()
    seed_tournament(session, payload)
    seed_ratings(session, ROOT / "data/seed/elo-ratings-2026.json")
    session.flush()
    return payload


def test_ensure_knockout_placeholders_creates_official_schedule(db_session):
    _seed_base(db_session)

    outcome = ensure_knockout_placeholders(db_session)

    knockout_matches = list(db_session.scalars(
        select(Match)
        .where(Match.stage != "group")
        .order_by(Match.bracket_position.asc())
    ))
    assert outcome["created"] == 32
    assert len(knockout_matches) == 32

    match_73 = db_session.get(Match, "2026-KO-073")
    match_90 = db_session.get(Match, "2026-KO-090")
    match_101 = db_session.get(Match, "2026-KO-101")
    match_103 = db_session.get(Match, "2026-KO-103")

    assert match_73.home_team_source == "A2"
    assert match_73.away_team_source == "B2"
    assert match_73.winner_to_match_id == "2026-KO-090"
    assert match_73.loser_to_match_id is None
    assert match_90.home_team_source == "W73"
    assert match_101.winner_to_match_id == "2026-KO-104"
    assert match_101.loser_to_match_id == "2026-KO-103"
    assert match_103.home_team_source == "L101"


def test_third_placed_ranking_keeps_group_codes(db_session):
    _seed_base(db_session)

    ranking = get_third_placed_ranking(db_session)

    assert len(ranking["qualified"]) == 8
    assert len(ranking["eliminated"]) == 4
    assert all(entry["group"] in set("ABCDEFGHIJKL") for entry in ranking["qualified"])
    assert all(entry["group"] in set("ABCDEFGHIJKL") for entry in ranking["eliminated"])


def test_refresh_advances_knockout_winner_into_next_match(db_session, monkeypatch):
    payload = _seed_base(db_session)
    ensure_knockout_placeholders(db_session)
    sync_knockout_state(db_session)

    match_73 = db_session.get(Match, "2026-KO-073")
    assert match_73.home_team_id is not None
    assert match_73.away_team_id is not None

    monkeypatch.setattr("app.services.refresh.fetch_and_store_market_data", lambda session: 0)
    monkeypatch.setattr("app.services.refresh.run_intelligence_pipeline", lambda session: False)

    updated = payload.model_copy(deep=True)
    updated.matches.append(TournamentMatch(
        id="2026-KO-073",
        group_code=None,
        home_team_id=match_73.home_team_id,
        away_team_id=match_73.away_team_id,
        kickoff=match_73.kickoff,
        venue=match_73.venue,
        status="final",
        home_score=1,
        away_score=0,
        home_advance=True,
        away_advance=False,
        went_to_extra_time=False,
        went_to_penalties=False,
        source_match_id="2026-KO-073",
    ))

    outcome = refresh_tournament(
        db_session,
        providers=[StaticProvider(updated)],
        recompute_predictions=False,
    )

    match_73 = db_session.get(Match, "2026-KO-073")
    match_90 = db_session.get(Match, "2026-KO-090")

    assert outcome.finalized_matches == 1
    assert match_73.status == "final"
    assert match_73.home_advance is True
    assert match_90.home_team_id == match_73.home_team_id
    assert match_90.home_team_source == "W73"
