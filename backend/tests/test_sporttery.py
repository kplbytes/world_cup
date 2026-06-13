import pytest

from app.providers.sporttery import SportteryUnavailable, normalize_had_prices, parse_response


def test_had_odds_are_normalized_without_overround():
    market = normalize_had_prices(home=1.80, draw=3.60, away=4.80)

    assert market.home + market.draw + market.away == pytest.approx(1.0)
    assert market.raw_overround > 1.0
    assert market.home > market.draw > market.away


def test_waf_html_degrades_to_unavailable():
    with pytest.raises(SportteryUnavailable, match="WAF"):
        parse_response("<!DOCTYPE html><title>WAF拦截页面</title>")


def test_invalid_price_is_rejected():
    with pytest.raises(ValueError):
        normalize_had_prices(home=0, draw=3.2, away=4.1)

