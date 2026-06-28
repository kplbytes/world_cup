"""Tournament qualification and bracket logic for 2026 FIFA World Cup (48 teams).

2026 FIFA World Cup knockout stage bracket:
- 12 groups (A-L), top 2 from each group (24 teams) + 8 best third-placed = 32 teams
- Round of 32 (matches 73-88) -> Round of 16 (89-96) -> QF (97-100) -> SF (101-102)
  -> Third place (103) -> Final (104)

Third-place team allocation follows the published official combination table.
When the group-stage picture is incomplete or invalid, the code falls back to
candidate-set allocation so partial bracket previews can still render.
"""

from __future__ import annotations

import logging
from typing import Any

from app.tournament.third_place import official_match_group_allocation

VALID_STAGES = [
    "group",
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
]

BRACKET_DISCLAIMER: str = (
    "本淘汰赛赛程采用 2026 世界杯官方第三名组合表生成。"
    "如果小组赛尚未全部结束或第三名集合不完整，才会回退到候选槽位预览。"
)

# ---------------------------------------------------------------------------
# Round of 32 bracket — official 2026 FIFA World Cup schedule
# Each entry: (match_number, home_source, away_source)
# Sources like "A1" mean group A winner; "A2" means group A runner-up.
# Sources like "3rd(A/B/C/D/F)" mean the best available third-placed team
#   from groups A, B, C, D, or F.
# ---------------------------------------------------------------------------

ROUND_OF_32_BRACKET: list[tuple[int, str, str]] = [
    (73, "A2", "B2"),
    (74, "E1", "3rd(A/B/C/D/F)"),
    (75, "F1", "C2"),
    (76, "C1", "F2"),
    (77, "I1", "3rd(C/D/F/G/H)"),
    (78, "E2", "I2"),
    (79, "A1", "3rd(C/E/F/H/I)"),
    (80, "L1", "3rd(E/H/I/J/K)"),
    (81, "D1", "3rd(B/E/F/I/J)"),
    (82, "G1", "3rd(A/E/H/I/J)"),
    (83, "K2", "L2"),
    (84, "H1", "J2"),
    (85, "B1", "3rd(E/F/G/I/J)"),
    (86, "J1", "H2"),
    (87, "K1", "3rd(D/E/I/J/L)"),
    (88, "D2", "G2"),
]

# ---------------------------------------------------------------------------
# Knockout bracket tree — maps each round to its match connections
# Format: { stage: [ (match_num, home_comes_from, away_comes_from), ... ] }
# ---------------------------------------------------------------------------

KNOCKOUT_BRACKET_TREE: dict[str, list[tuple[int, str, str]]] = {
    "round_of_32": ROUND_OF_32_BRACKET,
    "round_of_16": [
        (89, "W73", "W74"),
        (90, "W77", "W78"),
        (91, "W76", "W79"),
        (92, "W80", "W81"),
        (93, "W83", "W84"),
        (94, "W87", "W88"),
        (95, "W75", "W82"),
        (96, "W85", "W86"),
    ],
    "quarter_final": [
        (97, "W89", "W90"),
        (98, "W91", "W92"),
        (99, "W93", "W94"),
        (100, "W95", "W96"),
    ],
    "semi_final": [
        (101, "W97", "W98"),
        (102, "W99", "W100"),
    ],
    "third_place": [
        (103, "L101", "L102"),
    ],
    "final": [
        (104, "W101", "W102"),
    ],
}

# ---------------------------------------------------------------------------
# Third-place candidate groups for each match slot that needs a third-placed team
# ---------------------------------------------------------------------------

_THIRD_PLACE_SLOTS: list[tuple[int, str, set[str]]] = [
    # (match_number, side, candidate_groups)
    (74, "away", {"A", "B", "C", "D", "F"}),
    (77, "away", {"C", "D", "F", "G", "H"}),
    (79, "away", {"C", "E", "F", "H", "I"}),
    (80, "away", {"E", "H", "I", "J", "K"}),
    (81, "away", {"B", "E", "F", "I", "J"}),
    (82, "away", {"A", "E", "H", "I", "J"}),
    (85, "away", {"E", "F", "G", "I", "J"}),
    (87, "away", {"D", "E", "I", "J", "L"}),
]

# The order in which we fill third-place slots (matches 74,77,79,80,81,82,85,87)
_THIRD_PLACE_SLOT_ORDER: list[int] = [74, 77, 79, 80, 81, 82, 85, 87]


# ---------------------------------------------------------------------------
# Helper: parse a source string and resolve to a team dict
# ---------------------------------------------------------------------------

def _parse_group_source(source: str) -> tuple[str, int] | None:
    """Parse a source like 'A1' or 'L2' into (group_code, position)."""
    if len(source) == 2 and source[0].isalpha() and source[1].isdigit():
        return source[0].upper(), int(source[1])
    return None


def _resolve_team(
    source: str,
    winners: dict[str, dict[str, Any]],
    runners_up: dict[str, dict[str, Any]],
    third_placed_map: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a bracket source string to a team dict.

    Parameters
    ----------
    source : str
        E.g. "A1", "B2", "3rd(A/B/C/D/F)", "W73", "L101"
    winners : dict
        group_code -> team dict for group winners
    runners_up : dict
        group_code -> team dict for group runners-up
    third_placed_map : dict
        group_code -> team dict for qualified third-placed teams
        (only contains groups whose third-placed team qualified)
    """
    parsed = _parse_group_source(source)
    if parsed is not None:
        group_code, position = parsed
        if position == 1:
            return winners.get(group_code)
        elif position == 2:
            return runners_up.get(group_code)
        return None

    # Third-place source like "3rd(A/B/C/D/F)"
    if source.startswith("3rd(") and source.endswith(")"):
        groups_str = source[4:-1]
        candidate_groups = {g.strip().upper() for g in groups_str.split("/")}
        # Return the best available third-placed team from candidate groups
        # (The actual allocation is done by _allocate_third_placed_teams)
        for g in sorted(candidate_groups):
            if g in third_placed_map:
                return third_placed_map[g]
        return None

    return None


# ---------------------------------------------------------------------------
# Third-place team allocation
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


def _allocate_third_placed_teams(
    qualified_third: list[dict[str, Any]],
) -> dict[int, dict[str, Any] | None]:
    """Allocate third-placed teams to Round-of-32 matches.

    Uses the official combination table when 8 qualifying third-placed groups
    are known. Falls back to candidate-set allocation for incomplete or invalid
    inputs so the UI can still render a partial preview during group-stage play.
    """
    available: list[tuple[str, dict[str, Any]]] = []
    for team in qualified_third:
        group_code = team.get("group", "")
        if group_code:
            available.append((group_code.upper(), team))

    allocation: dict[int, dict[str, Any] | None] = {}

    official_groups = official_match_group_allocation([group for group, _ in available])
    if official_groups is not None:
        teams_by_group = {group: team for group, team in available}
        for match_num in _THIRD_PLACE_SLOT_ORDER:
            group_code = official_groups.get(match_num)
            allocation[match_num] = teams_by_group.get(group_code) if group_code else None
        return allocation

    for match_num in _THIRD_PLACE_SLOT_ORDER:
        # Find the candidate groups for this slot
        candidate_groups: set[str] = set()
        for mn, _side, groups in _THIRD_PLACE_SLOTS:
            if mn == match_num:
                candidate_groups = groups
                break

        # Try to pick the highest-ranked available team from candidate groups
        chosen_idx: int | None = None
        for idx, (g, _team) in enumerate(available):
            if g in candidate_groups:
                chosen_idx = idx
                break

        # Fallback: pick the highest-ranked remaining team
        if chosen_idx is None and available:
            chosen_idx = 0
            _logger.debug(
                "No candidate-group match for slot 3%s; assigning highest-ranked remaining 3rd-placed team %s",
                "/".join(sorted(candidate_groups)),
                available[0][1].get("team_id", "?"),
            )

        if chosen_idx is not None:
            _g, team = available.pop(chosen_idx)
            allocation[match_num] = team
        else:
            allocation[match_num] = None

    return allocation


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_knockout_matchups(
    group_standings: dict[str, list[dict[str, Any]]],
    third_placed_ranking: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate Round of 32 matchups from group standings.

    Parameters
    ----------
    group_standings : dict
        group_code -> list of team dicts sorted by standing (0=winner, 1=runner-up, 2=third, 3=fourth)
    third_placed_ranking : dict
        Must contain key "qualified": list of 8 third-placed team dicts,
        each with a "group" key indicating which group they come from.
        The list should be pre-sorted by ranking (best first).

    Returns
    -------
    list of matchup dicts with keys:
        match_number, stage, home_source, away_source, home_team, away_team
    """
    # Extract group winners and runners-up
    winners: dict[str, dict[str, Any]] = {}
    runners_up: dict[str, dict[str, Any]] = {}
    for group_code, standings in group_standings.items():
        code = group_code.upper()
        if len(standings) >= 1:
            winners[code] = standings[0]
        if len(standings) >= 2:
            runners_up[code] = standings[1]

    # Get qualified third-placed teams and allocate them to slots
    qualified_third: list[dict[str, Any]] = third_placed_ranking.get("qualified", [])
    third_allocation = _allocate_third_placed_teams(qualified_third)

    # Build a map: group_code -> team for third-placed teams that were allocated
    # (used by _resolve_team for the "3rd(...)" source strings)
    third_placed_map: dict[str, dict[str, Any]] = {}
    for _match_num, team in third_allocation.items():
        if team is not None:
            g = team.get("group", "").upper()
            if g:
                third_placed_map[g] = team

    matchups: list[dict[str, Any]] = []
    for match_num, home_src, away_src in ROUND_OF_32_BRACKET:
        # For third-place sources, resolve using the allocation map
        home_team = _resolve_team(home_src, winners, runners_up, third_placed_map)
        away_team = _resolve_team(away_src, winners, runners_up, third_placed_map)

        # Override: if the away source is a 3rd(...) and we have an allocation
        # for this match number, use the allocated team directly
        if away_src.startswith("3rd(") and match_num in third_allocation:
            away_team = third_allocation[match_num]

        matchups.append({
            "match_number": match_num,
            "stage": "round_of_32",
            "home_source": home_src,
            "away_source": away_src,
            "home_team": home_team,
            "away_team": away_team,
        })

    return matchups


def generate_bracket(
    group_standings: dict[str, list[dict[str, Any]]],
    third_placed_ranking: dict[str, Any],
    knockout_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Generate the full tournament bracket tree.

    Parameters
    ----------
    group_standings : dict
        group_code -> list of team dicts sorted by standing
    third_placed_ranking : dict
        Must contain "qualified": list of 8 third-placed team dicts
    knockout_results : list or None
        List of result dicts from completed knockout matches.
        Each result should have: match_number, stage, winner, loser

    Returns
    -------
    dict with keys for each knockout stage, each containing a list of matchup dicts.
    """
    knockout_results = knockout_results or []

    # Build a lookup: match_number -> result
    result_map: dict[int, dict[str, Any]] = {}
    for r in knockout_results:
        mn = r.get("match_number")
        if mn is not None:
            result_map[mn] = r

    # Round of 32
    r32_matchups = get_knockout_matchups(group_standings, third_placed_ranking)

    # Build a lookup: match_number -> matchup
    matchup_map: dict[int, dict[str, Any]] = {}
    for m in r32_matchups:
        matchup_map[m["match_number"]] = m

    # Generate subsequent rounds
    r16_matchups = _generate_next_round(
        KNOCKOUT_BRACKET_TREE["round_of_16"],
        "round_of_16",
        matchup_map,
        result_map,
    )
    for m in r16_matchups:
        matchup_map[m["match_number"]] = m

    qf_matchups = _generate_next_round(
        KNOCKOUT_BRACKET_TREE["quarter_final"],
        "quarter_final",
        matchup_map,
        result_map,
    )
    for m in qf_matchups:
        matchup_map[m["match_number"]] = m

    sf_matchups = _generate_next_round(
        KNOCKOUT_BRACKET_TREE["semi_final"],
        "semi_final",
        matchup_map,
        result_map,
    )
    for m in sf_matchups:
        matchup_map[m["match_number"]] = m

    tp_matchups = _generate_third_place(
        KNOCKOUT_BRACKET_TREE["third_place"],
        matchup_map,
        result_map,
    )

    final_matchups = _generate_next_round(
        KNOCKOUT_BRACKET_TREE["final"],
        "final",
        matchup_map,
        result_map,
    )

    return {
        "round_of_32": r32_matchups,
        "round_of_16": r16_matchups,
        "quarter_final": qf_matchups,
        "semi_final": sf_matchups,
        "third_place": tp_matchups,
        "final": final_matchups,
    }


# ---------------------------------------------------------------------------
# Internal: generate next round from bracket tree definition
# ---------------------------------------------------------------------------

def _generate_next_round(
    round_def: list[tuple[int, str, str]],
    stage: str,
    matchup_map: dict[int, dict[str, Any]],
    result_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate matchups for a round based on the bracket tree definition.

    Each entry in round_def is (match_number, home_from, away_from).
    home_from / away_from can be:
      - "W73"  -> winner of match 73
      - "L101" -> loser of match 101
    """
    matchups: list[dict[str, Any]] = []

    for match_num, home_from, away_from in round_def:
        home_team = _resolve_bracket_source(home_from, matchup_map, result_map)
        away_team = _resolve_bracket_source(away_from, matchup_map, result_map)

        matchups.append({
            "match_number": match_num,
            "stage": stage,
            "home_source": home_from,
            "away_source": away_from,
            "home_team": home_team,
            "away_team": away_team,
        })

    return matchups


def _resolve_bracket_source(
    source: str,
    matchup_map: dict[int, dict[str, Any]],
    result_map: dict[int, dict[str, Any]],
) -> dict[str, Any] | None:
    """Resolve a bracket source like 'W73' or 'L101' to a team dict.

    Uses the 'winner' or 'loser' from result_map when available,
    otherwise falls back to the 'home_team' from the matchup as placeholder.
    """
    if not source:
        return None

    kind = source[0]  # 'W' for winner, 'L' for loser
    try:
        ref_match_num = int(source[1:])
    except (ValueError, IndexError):
        return None

    # Check if the referenced match has a result
    result = result_map.get(ref_match_num)
    if result:
        if kind == "W":
            return result.get("winner")
        elif kind == "L":
            return result.get("loser")

    # No result yet — return None (match not yet decided)
    return None


def _generate_third_place(
    round_def: list[tuple[int, str, str]],
    matchup_map: dict[int, dict[str, Any]],
    result_map: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate the third-place match from semi-final losers."""
    matchups: list[dict[str, Any]] = []

    for match_num, home_from, away_from in round_def:
        home_team = _resolve_bracket_source(home_from, matchup_map, result_map)
        away_team = _resolve_bracket_source(away_from, matchup_map, result_map)

        matchups.append({
            "match_number": match_num,
            "stage": "third_place",
            "home_source": home_from,
            "away_source": away_from,
            "home_team": home_team,
            "away_team": away_team,
        })

    return matchups
