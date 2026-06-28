"""Tests for official third-place team allocation in bracket.py."""

import logging
import pytest

from app.tournament.bracket import (
    _allocate_third_placed_teams,
    _THIRD_PLACE_SLOTS,
    _THIRD_PLACE_SLOT_ORDER,
    get_knockout_matchups,
    ROUND_OF_32_BRACKET,
)
from app.tournament.qualification import _build_r32_matchups


def _make_third_team(team_id: str, group: str) -> dict:
    """Helper to create a third-placed team dict."""
    return {"team_id": team_id, "group": group}


class TestAllocateThirdPlacedTeams:
    """Test third-place allocation against the published combination table."""

    def test_official_combination_table_for_e_to_l_groups(self):
        """EFGHIJKL should follow the published row-1 allocation."""
        qualified = [
            _make_third_team("E3rd", "E"),
            _make_third_team("F3rd", "F"),
            _make_third_team("G3rd", "G"),
            _make_third_team("H3rd", "H"),
            _make_third_team("I3rd", "I"),
            _make_third_team("J3rd", "J"),
            _make_third_team("K3rd", "K"),
            _make_third_team("L3rd", "L"),
        ]
        allocation = _allocate_third_placed_teams(qualified)

        assert allocation[79]["team_id"] == "E3rd"  # A1 vs 3E
        assert allocation[85]["team_id"] == "J3rd"  # B1 vs 3J
        assert allocation[81]["team_id"] == "I3rd"  # D1 vs 3I
        assert allocation[74]["team_id"] == "F3rd"  # E1 vs 3F
        assert allocation[82]["team_id"] == "H3rd"  # G1 vs 3H
        assert allocation[77]["team_id"] == "G3rd"  # I1 vs 3G
        assert allocation[87]["team_id"] == "L3rd"  # K1 vs 3L
        assert allocation[80]["team_id"] == "K3rd"  # L1 vs 3K

    def test_official_combination_table_for_a_to_h_groups(self):
        """ABCDEFGH should follow the published row-495 allocation."""
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

        assert allocation[79]["team_id"] == "H3rd"
        assert allocation[85]["team_id"] == "G3rd"
        assert allocation[81]["team_id"] == "B3rd"
        assert allocation[74]["team_id"] == "C3rd"
        assert allocation[82]["team_id"] == "A3rd"
        assert allocation[77]["team_id"] == "F3rd"
        assert allocation[87]["team_id"] == "D3rd"
        assert allocation[80]["team_id"] == "E3rd"

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
        # Combination contains non-official groups, so the allocator must fall back
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

        with caplog.at_level(logging.DEBUG, logger="app.tournament.bracket"):
            allocation = _allocate_third_placed_teams(qualified)

        # M74 should still get a team (fallback)
        assert allocation[74] is not None
        # It should be the highest-ranked remaining team (G3rd)
        assert allocation[74]["team_id"] == "G3rd"

        # A debug message should have been logged about no candidate-group match
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

    def test_allocation_fills_all_eight_official_slots(self):
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

        filled = sum(1 for v in allocation.values() if v is not None)
        assert filled == 8

    def test_projection_builder_uses_same_official_table(self):
        group_standings = {
            group: [f"{group}1", f"{group}2", f"{group}3", f"{group}4"]
            for group in "ABCDEFGHIJKL"
        }
        qualified_third = [f"{group}3" for group in "ABCDEFGH"]
        third_group_map = {f"{group}3": group for group in "ABCDEFGH"}

        pairs = _build_r32_matchups(group_standings, qualified_third, third_group_map)

        assert pairs[1] == ("E1", "C3")   # M74: 1E vs 3C
        assert pairs[6] == ("A1", "H3")   # M79: 1A vs 3H
        assert pairs[8] == ("D1", "B3")   # M81: 1D vs 3B
        assert pairs[12] == ("B1", "G3")  # M85: 1B vs 3G
