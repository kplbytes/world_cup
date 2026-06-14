import pytest

from app.providers.football_data import FootballDataProvider, NotConfigured


def test_load_raises_not_configured_without_token(monkeypatch):
    monkeypatch.setattr("app.providers.football_data.settings.football_data_api_token", None)
    provider = FootballDataProvider(token=None)
    with pytest.raises(NotConfigured, match="not configured"):
        provider.load()


def test_load_raises_not_configured_with_empty_token():
    provider = FootballDataProvider(token="")
    # Empty string is falsy, but the provider checks `self._token` which
    # falls back to settings. Override settings token as well.
    provider._token = ""
    with pytest.raises(NotConfigured, match="not configured"):
        provider.load()


def test_not_configured_is_a_runtime_error():
    assert issubclass(NotConfigured, RuntimeError)


def test_normalize_match_accepts_football_data_group_code():
    match = FootballDataProvider._normalize_match({
        "id": 123,
        "utcDate": "2026-06-14T04:00:00Z",
        "status": "FINISHED",
        "stage": "GROUP_STAGE",
        "group": "GROUP_D",
        "homeTeam": {"tla": "AUS", "shortName": "Australia"},
        "awayTeam": {"tla": "TUR", "shortName": "Türkiye"},
        "score": {"fullTime": {"home": 2, "away": 0}},
        "venue": "Vancouver",
    })

    assert match.id == "2026-D-AUS-TUR-2026-06-14"
    assert match.group_code == "D"
    assert match.status == "final"
    assert (match.home_score, match.away_score) == (2, 0)


def test_normalize_match_maps_provider_team_code_to_canonical_code():
    match = FootballDataProvider._normalize_match({
        "id": 124,
        "utcDate": "2026-06-15T22:00:00Z",
        "status": "SCHEDULED",
        "stage": "GROUP_STAGE",
        "group": "GROUP_H",
        "homeTeam": {"tla": "KSA", "shortName": "Saudi Arabia"},
        "awayTeam": {"tla": "URY", "shortName": "Uruguay"},
        "score": {"fullTime": {"home": None, "away": None}},
        "venue": None,
    })

    assert match.away_team_id == "URU"
    assert match.id == "2026-H-KSA-URU-2026-06-15"
