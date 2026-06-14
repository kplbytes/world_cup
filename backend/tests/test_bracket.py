"""Tests for third-place team allocation in bracket.py."""

import logging
import pytest

from app.tournament.bracket import (
    _allocate_third_placed_teams,
    _THIRD_PLACE_SLOTS,
    _THIRD_PLACE_SLOT_ORDER,
    get_knockout_matchups,
    ROUND_OF_32_BRACKET,
)


def _make_third_team(team_id: str, group: str) -> dict:
    """Helper to create a third-placed team dict."""
    return {"team_id": team_id, "group": group}


class TestAllocateThirdPlacedTeams:
    """Test the simplified greedy third-place allocation algorithm."""

    def test_only_candidate_groups_used(self):
        """When A/B/C/D/F only have C and F as qualified third-placed,
        M74 must pick from C or F (not from other groups)."""
        qualified = [
            _make_third_team("C3rd", "C"),
            _make_third_team("F3rd", "F"),
            _make_third_team("G3rd", "G"),
            _make_third_team("H3rd", "H"),
            _make_third_team("I3rd", "I"),
            _make_third_team("J3rd", "J"),
            _make_third_team("K3rd", "K"),
            _make_third_team("L3rd", "L"),
        ]
        allocation = _allocate_third_placed_teams(qualified)

        # M74 candidates are A/B/C/D/F — only C and F qualify
        # So M74 should get C3rd (highest-ranked from candidates)
        assert allocation[74] is not None
        assert allocation[74]["team_id"] == "C3rd"

    def test_no_duplicate_allocation(self):
        """A third-placed team cannot be allocated to two different matches."""
        qualified = [
            _make_third_team("A3rd", "A"),
            _make_third_team("B3rd", "B"),
            _make_third_team("C3rd", "C"),
            _make_third_team("D3rd", "D"),
            _make_third_team("E3rd", "E"),
            _make_third_team("F3rd", "F"),
            _make_third_team("G3rd", "G"),
            _make_third_team("H3rd", "H"),
        ]
        allocation = _allocate_third_placed_teams(qualified)

        # Collect all allocated team_ids
        allocated_ids = []
        for match_num in _THIRD_PLACE_SLOT_ORDER:
            team = allocation.get(match_num)
            if team is not None:
                allocated_ids.append(team["team_id"])

        # No duplicates
        assert len(allocated_ids) == len(set(allocated_ids)), (
            f"Duplicate allocation found: {allocated_ids}"
        )

    def test_fallback_when_no_candidate_available(self, caplog):
        """When no candidate group has a qualified third-placed team,
        the fallback picks the highest-ranked remaining team and logs a warning."""
        # Only groups G, H, I, J, K, L have third-placed teams
        # M74 candidates are A/B/C/D/F — none of them qualified
        qualified = [
            _make_third_team("G3rd", "G"),
            _make_third_team("H3rd", "H"),
            _make_third_team("I3rd", "I"),
            _make_third_team("J3rd", "J"),
            _make_third_team("K3rd", "K"),
            _make_third_team("L3rd", "L"),
            _make_third_team("M3rd", "M"),  # fictional group, no candidate
            _make_third_team("N3rd", "N"),  # fictional group, no candidate
        ]

        with caplog.at_level(logging.WARNING):
            allocation = _allocate_third_placed_teams(qualified)

        # M74 should still get a team (fallback)
        assert allocation[74] is not None
        # It should be the highest-ranked remaining team (G3rd)
        assert allocation[74]["team_id"] == "G3rd"

        # A warning should have been logged about no candidate-group match
        assert any("No candidate-group match" in record.message for record in caplog.records)

    def test_same_team_not_in_two_matches(self):
        """Verify end-to-end: the same third-placed team does not appear
        in two different Round of 32 matches."""
        qualified = [
            _make_third_team("A3rd", "A"),
            _make_third_team("C3rd", "C"),
            _make_third_team("E3rd", "E"),
            _make_third_team("F3rd", "F"),
            _make_third_team("H3rd", "H"),
            _make_third_team("I3rd", "I"),
            _make_third_team("J3rd", "J"),
            _make_third_team("D3rd", "D"),
        ]

        # Build group standings (just need winners and runners-up for 12 groups)
        group_standings = {}
        for group in "ABCDEFGHIJKL":
            group_standings[group] = [
                {"team_id": f"{group}1st", "group": group},
                {"team_id": f"{group}2nd", "group": group},
                {"team_id": f"{group}3rd", "group": group},
                {"team_id": f"{group}4th", "group": group},
            ]

        third_placed_ranking = {"qualified": qualified}
        matchups = get_knockout_matchups(group_standings, third_placed_ranking)

        # Collect all third-placed teams that appear in matchups
        third_teams_in_matchups = []
        for m in matchups:
            # Check if away_source starts with "3rd("
            if m["away_source"].startswith("3rd("):
                if m["away_team"] is not None:
                    third_teams_in_matchups.append(m["away_team"]["team_id"])

        # No duplicates
        assert len(third_teams_in_matchups) == len(set(third_teams_in_matchups)), (
            f"Same third-placed team appears in multiple matches: {third_teams_in_matchups}"
        )

        # All 8 third-placed teams should be allocated
        assert len(third_teams_in_matchups) == 8

    def test_allocation_respects_slot_order(self):
        """Earlier slots get higher-ranked teams from their candidate groups."""
        qualified = [
            _make_third_team("A3rd", "A"),
            _make_third_team("B3rd", "B"),
            _make_third_team("C3rd", "C"),
            _make_third_team("D3rd", "D"),
            _make_third_team("E3rd", "E"),
            _make_third_team("F3rd", "F"),
            _make_third_team("G3rd", "G"),
            _make_third_team("H3rd", "H"),
        ]
        allocation = _allocate_third_placed_teams(qualified)

        # M74 candidates: A/B/C/D/F → A3rd is first in list and in candidates
        assert allocation[74]["team_id"] == "A3rd"

        # M77 candidates: C/D/F/G/H → C3rd is next highest-ranked in candidates
        # But A3rd was already used, so C3rd should be picked
        assert allocation[77]["team_id"] == "C3rd"

        # Verify all 8 slots are filled
        filled = sum(1 for v in allocation.values() if v is not None)
        assert filled == 8
