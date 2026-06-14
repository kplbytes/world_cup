import pytest

from app.providers.sporttery import SportteryUnavailable, normalize_had_prices, parse_response
from app.services.market import SportteryRemoteProvider


def test_had_odds_are_normalized_without_overround():
    market = normalize_had_prices(home=1.80, draw=3.60, away=4.80)

    assert market.home + market.draw + market.away == pytest.approx(1.0)
    assert market.raw_overround > 1.0
    assert market.home > market.draw > market.away


def test_waf_html_degrades_to_unavailable():
    with pytest.raises(SportteryUnavailable, match="WAF"):
        parse_response("<!DOCTYPE html><title>WAF拦截页面</title>")


def test_string_error_code_zero_is_accepted():
    payload = parse_response('{"errorCode":"0","value":{"matchInfoList":[]}}')

    assert payload["errorCode"] == "0"


def test_invalid_price_is_rejected():
    with pytest.raises(ValueError):
        normalize_had_prices(home=0, draw=3.2, away=4.1)


def test_extract_matches_supports_nested_submatch_list():
    rows = SportteryRemoteProvider()._extract_matches({
        "value": {
            "matchInfoList": [
                {
                    "businessDate": "2026-06-14",
                    "subMatchList": [
                        {
                            "matchNum": 7009,
                            "matchDate": "2026-06-15",
                            "matchTime": "01:00:00",
                            "homeTeamAbbName": "德国",
                            "awayTeamAbbName": "库拉索",
                            "had": {"h": "1.86", "d": "3.38", "a": "3.38"},
                            "oddsList": [],
                        }
                    ],
                }
            ]
        }
    })

    assert rows == [{
        "match_num": "7009",
        "home_team": "德国",
        "away_team": "库拉索",
        "match_date": "2026-06-15 01:00:00",
        "had_home": 1.86,
        "had_draw": 3.38,
        "had_away": 3.38,
    }]
