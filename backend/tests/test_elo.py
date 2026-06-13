from datetime import date

import pytest

from app.prediction.elo import RatedMatch, recent_form, replay_elo, update_elo


def test_upset_moves_both_ratings_by_equal_and_opposite_amounts():
    result = update_elo(home=1800, away=1500, home_goals=0, away_goals=1, weight=40)

    assert result.home < 1800
    assert result.away > 1500
    assert result.home + result.away == pytest.approx(3300)


def test_replay_does_not_use_matches_after_cutoff():
    matches = [
        RatedMatch(date(2026, 1, 1), "A", "B", 1, 0),
        RatedMatch(date(2026, 7, 1), "B", "A", 4, 0),
    ]

    ratings = replay_elo(matches, cutoff=date(2026, 6, 1))

    assert ratings["A"] > ratings["B"]


def test_recent_form_uses_latest_five_results_in_chronological_display_order():
    matches = [
        RatedMatch(date(2026, month, 1), "A", "B", home, away)
        for month, (home, away) in enumerate(
            [(2, 0), (0, 0), (0, 1), (3, 1), (1, 1), (2, 1)],
            start=1,
        )
    ]

    assert recent_form("A", matches) == "DLWDW"

