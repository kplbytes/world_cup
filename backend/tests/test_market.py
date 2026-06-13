from datetime import datetime, timezone

import pytest

from app.models import MarketSnapshot
from app.services.market import (
    DivergenceResult,
    compute_divergence,
    divergence_level,
    _fuzzy_match,
    _name_matches,
    _sporttery_match_date,
)


class TestComputeDivergence:
    def _market(self, home=0.46, draw=0.28, away=0.26, overround=1.08):
        """Create a minimal MarketSnapshot stub."""

        class Stub:
            home_probability = home
            draw_probability = draw
            away_probability = away
            raw_overround = overround

        return Stub()

    def test_low_divergence_when_model_and_market_agree(self):
        result = compute_divergence(
            {"home_win": 0.48, "draw": 0.27, "away_win": 0.25},
            self._market(0.46, 0.28, 0.26),
        )
        assert result.level == "低"
        assert result.max_divergence < 0.08

    def test_medium_divergence_for_moderate_disagreement(self):
        result = compute_divergence(
            {"home_win": 0.58, "draw": 0.25, "away_win": 0.17},
            self._market(0.46, 0.28, 0.26),
        )
        assert result.level == "中"
        assert 0.08 <= result.max_divergence < 0.18

    def test_high_divergence_for_major_disagreement(self):
        result = compute_divergence(
            {"home_win": 0.70, "draw": 0.20, "away_win": 0.10},
            self._market(0.46, 0.28, 0.26),
        )
        assert result.level == "高"
        assert result.max_divergence >= 0.18

    def test_divergence_signs_correct(self):
        result = compute_divergence(
            {"home_win": 0.60, "draw": 0.25, "away_win": 0.15},
            self._market(0.50, 0.30, 0.20),
        )
        assert result.home_diff > 0   # model higher on home
        assert result.draw_diff < 0   # model lower on draw
        assert result.away_diff < 0   # model lower on away


class TestDivergenceLevel:
    def test_low(self):
        assert divergence_level(0.05) == "低"

    def test_medium(self):
        assert divergence_level(0.12) == "中"

    def test_high(self):
        assert divergence_level(0.25) == "高"

    def test_boundary_low(self):
        assert divergence_level(0.0799) == "低"

    def test_boundary_medium(self):
        assert divergence_level(0.08) == "中"

    def test_boundary_high(self):
        assert divergence_level(0.18) == "高"


class TestFuzzyMatch:
    def test_exact_match(self):
        assert _fuzzy_match("brazil", "brazil") is True

    def test_containment(self):
        assert _fuzzy_match("korea republic", "korea") is True

    def test_no_match(self):
        assert _fuzzy_match("brazil", "germany") is False

    def test_provider_alias_can_match_localized_name(self):
        assert _name_matches({"korea republic", "south korea", "韩国"}, "韩国") is True

    def test_sporttery_match_date_uses_shanghai_timezone(self):
        kickoff = datetime(2026, 6, 13, 16, 30, tzinfo=timezone.utc)

        assert _sporttery_match_date(kickoff) == "2026-06-14"
