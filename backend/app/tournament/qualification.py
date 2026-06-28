"""Tournament qualification probability computation.

Uses the official 2026 World Cup bracket structure for knockout matchups.
48 teams, 12 groups (A-L) of 4. Top 2 per group (24) + 8 best 3rd-placed = 32 in knockout.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.tournament.bracket import ROUND_OF_32_BRACKET
from app.tournament.third_place import official_match_group_allocation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Official 2026 World Cup bracket – Round of 32 matchups
# ---------------------------------------------------------------------------
# Each entry: (match_number, home_slot, away_slot)
# Slot formats:
#   "X1" / "X2"  – 1st / 2nd place in group X  (X = A..L)
#   "3A/B/…"     – best available 3rd-placed team from the listed candidate groups
#
# Source: FIFA match schedule (matches 73-88)

OFFICIAL_R32_MATCHUPS: list[tuple[int, str, str]] = [
    (73, "2A",  "2B"),             # M73
    (74, "1E",  "3A/B/C/D/F"),    # M74
    (75, "1F",  "2C"),             # M75
    (76, "1C",  "2F"),             # M76
    (77, "1I",  "3C/D/F/G/H"),    # M77
    (78, "2E",  "2I"),             # M78
    (79, "1A",  "3C/E/F/H/I"),    # M79
    (80, "1L",  "3E/H/I/J/K"),    # M80
    (81, "1D",  "3B/E/F/I/J"),    # M81
    (82, "1G",  "3A/E/H/I/J"),    # M82
    (83, "2K",  "2L"),             # M83
    (84, "1H",  "2J"),             # M84
    (85, "1B",  "3E/F/G/I/J"),    # M85
    (86, "1J",  "2H"),             # M86
    (87, "1K",  "3D/E/I/J/L"),    # M87
    (88, "2D",  "2G"),             # M88
]

# Round of 16: (match_number, r32_home_match, r32_away_match)
OFFICIAL_R16_MATCHUPS: list[tuple[int, int, int]] = [
    (89,  74, 77),   # W74 vs W77
    (90,  73, 75),   # W73 vs W75
    (91,  76, 78),   # W76 vs W78
    (92,  79, 80),   # W79 vs W80
    (93,  83, 84),   # W83 vs W84
    (94,  81, 82),   # W81 vs W82
    (95,  86, 88),   # W86 vs W88
    (96,  85, 87),   # W85 vs W87
]

# Quarter-finals
OFFICIAL_QF_MATCHUPS: list[tuple[int, int, int]] = [
    (97, 89, 90),   # W89 vs W90
    (98, 93, 94),   # W93 vs W94
    (99, 91, 92),   # W91 vs W92
    (100, 95, 96),  # W95 vs W96
]

# Semi-finals
OFFICIAL_SF_MATCHUPS: list[tuple[int, int, int]] = [
    (101, 97, 98),  # W97 vs W98
    (102, 99, 100), # W99 vs W100
]

# Final
OFFICIAL_FINAL_MATCHUP: tuple[int, int, int] = (104, 101, 102)  # W101 vs W102

ALL_GROUPS = [chr(ord("A") + i) for i in range(12)]  # A..L


@dataclass(frozen=True)
class TeamProjection:
    """Projection for a team's tournament progression."""
    team_id: str
    group_qualify: float
    round_of_32: float
    round_of_16: float
    quarter_final: float
    semi_final: float
    final: float
    champion: float


# ---------------------------------------------------------------------------
# Third-place allocation helpers
# ---------------------------------------------------------------------------

def _parse_third_slot_candidates(slot: str) -> list[str]:
    """Parse a slot like '3A/B/C/D/F' into candidate group codes ['A','B','C','D','F']."""
    if not slot.startswith("3"):
        return []
    groups_part = slot[1:]
    return [g.strip() for g in groups_part.split("/") if g.strip()]


def _allocate_third_placed_teams(
    qualified_third: list[str],
    third_group_map: dict[str, str],
) -> dict[str, str]:
    """Allocate the 8 qualified third-placed teams to bracket slots."""
    third_slots: list[str] = []
    slot_by_match_number: dict[int, str] = {}
    for match_number, home_slot, away_slot in OFFICIAL_R32_MATCHUPS:
        if home_slot.startswith("3"):
            third_slots.append(home_slot)
        if away_slot.startswith("3"):
            third_slots.append(away_slot)
            slot_by_match_number[match_number] = away_slot

    official_groups = official_match_group_allocation(
        [third_group_map.get(team_id, "") for team_id in qualified_third]
    )
    if official_groups is not None:
        team_by_group = {third_group_map.get(team_id, ""): team_id for team_id in qualified_third}
        allocation: dict[str, str] = {}
        for match_number, group_code in official_groups.items():
            slot = slot_by_match_number.get(match_number)
            team_id = team_by_group.get(group_code)
            if slot and team_id:
                allocation[slot] = team_id
        if allocation:
            return allocation

    remaining = list(qualified_third)
    allocation: dict[str, str] = {}

    for slot in third_slots:
        candidates = _parse_third_slot_candidates(slot)
        matched = False
        for i, team_id in enumerate(remaining):
            group = third_group_map.get(team_id, "")
            if group in candidates:
                allocation[slot] = team_id
                remaining.pop(i)
                matched = True
                break

        if not matched:
            if remaining:
                logger.debug(
                    "No candidate-group match for slot %s; "
                    "assigning highest-ranked remaining 3rd-placed team %s",
                    slot, remaining[0],
                )
                allocation[slot] = remaining.pop(0)
            else:
                logger.debug(
                    "No third-placed team available for slot %s", slot,
                )

    return allocation


def _resolve_slot(
    slot: str,
    group_standings: dict[str, list[str]],
    third_allocation: dict[str, str],
) -> str | None:
    """Resolve a bracket slot string to a team_id.

    Slot formats:
      "X1" or "1X" -> 1st place in group X
      "X2" or "2X" -> 2nd place in group X
      "3A/B/…" -> allocated third-placed team via third_allocation

    Both "A1" and "1A" are accepted for group position slots.
    """
    if slot.startswith("3"):
        return third_allocation.get(slot)

    if len(slot) >= 2:
        # Determine which char is the group code (letter) and which is position (digit)
        if slot[0].isalpha() and slot[1].isdigit():
            group_code = slot[0]
            position = int(slot[1])
        elif slot[0].isdigit() and slot[1].isalpha():
            position = int(slot[0])
            group_code = slot[1]
        else:
            return None

        standings = group_standings.get(group_code, [])
        idx = position - 1
        if 0 <= idx < len(standings):
            return standings[idx]

    return None


# ---------------------------------------------------------------------------
# Elo-based match simulation
# ---------------------------------------------------------------------------

def _elo_win_prob(home_elo: float, away_elo: float) -> float:
    """Compute home-team win probability from Elo ratings (including home advantage)."""
    HOME_ADVANTAGE = 68.0  # ~68 Elo points home advantage
    effective_home = home_elo + HOME_ADVANTAGE
    return 1.0 / (1.0 + 10.0 ** ((away_elo - effective_home) / 400.0))


def _simulate_match(rng, home: str, away: str, elos: dict[str, float]) -> str:
    """Simulate a single knockout match and return the winner's team_id."""
    home_elo = elos.get(home, 1500.0)
    away_elo = elos.get(away, 1500.0)
    home_win_prob = _elo_win_prob(home_elo, away_elo)

    # 70% decided in 90 min, 30% goes to extra time / penalties
    if rng.random() < 0.70:
        return home if rng.random() < home_win_prob else away
    else:
        et_prob = 0.5 + (home_win_prob - 0.5) * 0.3
        return home if rng.random() < et_prob else away


def _simulate_round(
    rng,
    matchups: list[tuple[str, str]],
    elos: dict[str, float],
) -> list[str]:
    """Simulate a knockout round from a list of (home, away) pairs. Return winners.

    If a matchup has one side as None, the other side advances with a warning.
    Raises ValueError if both sides of a matchup are None (invalid bracket).
    """
    winners = []
    for i, (home, away) in enumerate(matchups):
        if home is None and away is None:
            logger.warning("Matchup %d has no teams – both sides are None", i)
            raise ValueError(
                f"Matchup {i} has no teams (both home and away are None). "
                "This indicates an invalid bracket configuration."
            )
        if home is None or away is None:
            winner = home if home is not None else away
            logger.warning(
                "Matchup %d has an unpaired team (%s) – opponent is None; "
                "advancing by default",
                i, winner,
            )
            winners.append(winner)
            continue
        winners.append(_simulate_match(rng, home, away, elos))
    return winners


# ---------------------------------------------------------------------------
# Bracket-tree knockout simulation
# ---------------------------------------------------------------------------

def _build_r32_matchups(
    group_standings: dict[str, list[str]],
    qualified_third: list[str],
    third_group_map: dict[str, str],
) -> list[tuple[str, str]]:
    """Build the 16 Round-of-32 (home, away) pairs using the official bracket."""
    third_allocation = _allocate_third_placed_teams(qualified_third, third_group_map)

    matchups: list[tuple[str, str]] = []
    for _, home_slot, away_slot in OFFICIAL_R32_MATCHUPS:
        home = _resolve_slot(home_slot, group_standings, third_allocation)
        away = _resolve_slot(away_slot, group_standings, third_allocation)
        matchups.append((home, away))

    return matchups


def _advance_bracket(
    prev_winners: dict[int, str],
    next_round_matchups: list[tuple[int, int, int]],
) -> list[tuple[str, str]]:
    """Given winners keyed by match number, produce the next round's (home, away) pairs."""
    pairs: list[tuple[str, str]] = []
    for _, home_match, away_match in next_round_matchups:
        home = prev_winners.get(home_match)
        away = prev_winners.get(away_match)
        pairs.append((home, away))
    return pairs


def simulate_knockout(
    group_standings: dict[str, list[str]],
    qualified_third: list[str],
    team_elos: dict[str, float],
    seed: int = 20260613,
) -> dict[str, dict[str, int]]:
    """Simulate the full knockout stage once using the official bracket.

    Args:
        group_standings: group_code -> [1st_team_id, 2nd, 3rd, 4th]
        qualified_third: list of 8 best third-placed team_ids (ranked best-first)
        team_elos: team_id -> Elo rating
        seed: RNG seed for this single simulation

    Returns:
        dict mapping team_id -> {stage_name: count (0 or 1)} for stages reached.
        Stages: round_of_32, round_of_16, quarter_final, semi_final, final, champion
    """
    import numpy as np

    rng = np.random.default_rng(seed)

    third_group_map: dict[str, str] = {}
    for group_code, standings in group_standings.items():
        if len(standings) >= 3:
            third_group_map[standings[2]] = group_code

    return _run_knockout_bracket(rng, group_standings, qualified_third, third_group_map, team_elos)


def _run_knockout_bracket(
    rng,
    group_standings: dict[str, list[str]],
    qualified_third: list[str],
    third_group_map: dict[str, str],
    team_elos: dict[str, float],
) -> dict[str, dict[str, int]]:
    """Core knockout bracket simulation shared by simulate_knockout and compute_projections."""
    # --- Round of 32 ---
    r32_pairs = _build_r32_matchups(group_standings, qualified_third, third_group_map)
    r32_winners_list = _simulate_round(rng, r32_pairs, team_elos)

    r32_match_nums = [mn for mn, _, _ in OFFICIAL_R32_MATCHUPS]
    r32_winners: dict[int, str] = {}
    for match_num, winner in zip(r32_match_nums, r32_winners_list):
        if winner is not None:
            r32_winners[match_num] = winner

    progression: dict[str, dict[str, int]] = {}
    for home, away in r32_pairs:
        for team in (home, away):
            if team is not None:
                progression.setdefault(team, {})["round_of_32"] = 1
    for w in r32_winners_list:
        if w is not None:
            progression.setdefault(w, {})["round_of_16"] = 1

    # --- Round of 16 ---
    r16_pairs = _advance_bracket(r32_winners, OFFICIAL_R16_MATCHUPS)
    r16_winners_list = _simulate_round(rng, r16_pairs, team_elos)

    r16_match_nums = [mn for mn, _, _ in OFFICIAL_R16_MATCHUPS]
    r16_winners: dict[int, str] = {}
    for match_num, winner in zip(r16_match_nums, r16_winners_list):
        if winner is not None:
            r16_winners[match_num] = winner

    for w in r16_winners_list:
        if w is not None:
            progression.setdefault(w, {})["quarter_final"] = 1

    # --- Quarter-finals ---
    qf_pairs = _advance_bracket(r16_winners, OFFICIAL_QF_MATCHUPS)
    qf_winners_list = _simulate_round(rng, qf_pairs, team_elos)

    qf_match_nums = [mn for mn, _, _ in OFFICIAL_QF_MATCHUPS]
    qf_winners: dict[int, str] = {}
    for match_num, winner in zip(qf_match_nums, qf_winners_list):
        if winner is not None:
            qf_winners[match_num] = winner

    for w in qf_winners_list:
        if w is not None:
            progression.setdefault(w, {})["semi_final"] = 1

    # --- Semi-finals ---
    sf_pairs = _advance_bracket(qf_winners, OFFICIAL_SF_MATCHUPS)
    sf_winners_list = _simulate_round(rng, sf_pairs, team_elos)

    sf_match_nums = [mn for mn, _, _ in OFFICIAL_SF_MATCHUPS]
    sf_winners: dict[int, str] = {}
    for match_num, winner in zip(sf_match_nums, sf_winners_list):
        if winner is not None:
            sf_winners[match_num] = winner

    for w in sf_winners_list:
        if w is not None:
            progression.setdefault(w, {})["final"] = 1

    # --- Final ---
    final_pair = _advance_bracket(sf_winners, [OFFICIAL_FINAL_MATCHUP])
    final_winners = _simulate_round(rng, final_pair, team_elos)

    if final_winners:
        progression.setdefault(final_winners[0], {})["champion"] = 1

    return progression


# ---------------------------------------------------------------------------
# Monte Carlo projection (group placement aware)
# ---------------------------------------------------------------------------

def compute_projections(
    group_placement_probs: dict[str, dict[str, float]],
    team_elos: dict[str, float],
    team_group_map: dict[str, str] | None = None,
    iterations: int = 10_000,
    seed: int = 20260613,
) -> list[TeamProjection]:
    """Compute full tournament progression probabilities via Monte Carlo.

    Uses the official 2026 World Cup bracket to determine knockout matchups.

    Args:
        group_placement_probs: team_id -> {"1st": p1, "2nd": p2, "3rd": p3, "4th": p4}
            Probability of each team finishing in each group position.
        team_elos: team_id -> Elo rating
        team_group_map: team_id -> group_code (A-L). Required for bracket construction.
        iterations: number of Monte Carlo iterations
        seed: random seed
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    team_ids = sorted(group_placement_probs.keys())

    if team_group_map is None:
        raise ValueError(
            "team_group_map is required: map each team_id to its group code (A-L)"
        )

    # Build group -> [team_ids] mapping
    groups: dict[str, list[str]] = {}
    for tid in team_ids:
        gc = team_group_map.get(tid)
        if gc is None:
            raise ValueError(f"Team {tid} not found in team_group_map")
        groups.setdefault(gc, []).append(tid)

    # Track progression counts
    group_qualify_counts = {t: 0 for t in team_ids}
    r32_counts = {t: 0 for t in team_ids}
    r16_counts = {t: 0 for t in team_ids}
    qf_counts = {t: 0 for t in team_ids}
    sf_counts = {t: 0 for t in team_ids}
    final_counts = {t: 0 for t in team_ids}
    champion_counts = {t: 0 for t in team_ids}

    for _ in range(iterations):
        # Step a: Sample group placements for each group
        group_standings: dict[str, list[str]] = {}
        all_third_placed: list[tuple[str, str]] = []  # (team_id, group_code)

        for group_code in ALL_GROUPS:
            group_teams = groups.get(group_code, [])
            if not group_teams:
                continue

            placement = _sample_group_placement(rng, group_teams, group_placement_probs)
            group_standings[group_code] = placement

            if len(placement) >= 3:
                all_third_placed.append((placement[2], group_code))

        # Step b: Determine the 8 best third-placed teams (ranked by Elo as proxy)
        all_third_placed.sort(
            key=lambda x: team_elos.get(x[0], 1500.0), reverse=True
        )
        qualified_third = [tid for tid, _ in all_third_placed[:8]]
        third_group_map = {tid: gc for tid, gc in all_third_placed[:8]}

        # Step c & d: Simulate knockout using the official bracket
        progression = _run_knockout_bracket(
            rng, group_standings, qualified_third, third_group_map, team_elos,
        )

        # Accumulate counts
        for tid in team_ids:
            if tid in progression:
                stages = progression[tid]
                r32_counts[tid] += stages.get("round_of_32", 0)
                r16_counts[tid] += stages.get("round_of_16", 0)
                qf_counts[tid] += stages.get("quarter_final", 0)
                sf_counts[tid] += stages.get("semi_final", 0)
                final_counts[tid] += stages.get("final", 0)
                champion_counts[tid] += stages.get("champion", 0)

        # Group qualification: 1st or 2nd in group, or one of the 8 best 3rd
        for group_code, placement in group_standings.items():
            for i, tid in enumerate(placement):
                if i < 2:
                    group_qualify_counts[tid] += 1
                elif i == 2 and tid in qualified_third:
                    group_qualify_counts[tid] += 1

    # Build projections
    projections = []
    for tid in team_ids:
        projections.append(TeamProjection(
            team_id=tid,
            group_qualify=group_qualify_counts[tid] / iterations,
            round_of_32=r32_counts[tid] / iterations,
            round_of_16=r16_counts[tid] / iterations,
            quarter_final=qf_counts[tid] / iterations,
            semi_final=sf_counts[tid] / iterations,
            final=final_counts[tid] / iterations,
            champion=champion_counts[tid] / iterations,
        ))

    return sorted(projections, key=lambda p: p.champion, reverse=True)


def _sample_group_placement(
    rng,
    group_teams: list[str],
    group_placement_probs: dict[str, dict[str, float]],
) -> list[str]:
    """Sample a group placement ordering (1st/2nd/3rd/4th) for teams in a group.

    Iteratively pick the team for each position based on their probability
    of finishing in that position, conditioned on previously placed teams.

    Returns:
        List of team_ids in order [1st, 2nd, 3rd, 4th]
    """
    positions = ["1st", "2nd", "3rd", "4th"]
    remaining = list(group_teams)
    placement: list[str] = []

    for pos in positions:
        if not remaining:
            break

        if len(remaining) == 1:
            placement.append(remaining[0])
            break

        probs = []
        for tid in remaining:
            p = group_placement_probs.get(tid, {}).get(pos, 0.0)
            probs.append(max(p, 1e-10))

        total = sum(probs)
        if total <= 0:
            probs = [1.0] * len(remaining)
            total = len(remaining)

        probs = [p / total for p in probs]

        chosen_idx = rng.choice(len(remaining), p=probs)
        chosen = remaining.pop(chosen_idx)
        placement.append(chosen)

    return placement
