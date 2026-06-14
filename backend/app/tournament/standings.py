"""Tournament standings computation with group ranking."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.standings import MatchResult, rank_group, rank_third_placed
from app.models import Match, StandingSnapshot, Team


def get_current_standings(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Get current group standings for all groups."""
    teams = list(session.scalars(select(Team).order_by(Team.group_code, Team.id)))
    matches = list(session.scalars(select(Match).where(Match.stage == "group").order_by(Match.kickoff, Match.id)))

    groups: dict[str, list[str]] = {}
    for team in teams:
        groups.setdefault(team.group_code, []).append(team.id)

    completed = [
        MatchResult(m.home_team_id, m.away_team_id, m.home_score, m.away_score)
        for m in matches
        if m.status == "final" and m.home_score is not None and m.away_score is not None
        and m.home_team_id and m.away_team_id
    ]

    result: dict[str, list[dict[str, Any]]] = {}
    team_names = {t.id: t.short_name for t in teams}

    for group_code, group_teams in sorted(groups.items()):
        team_set = set(group_teams)
        group_results = [r for r in completed if r.home_team_id in team_set and r.away_team_id in team_set]
        table = rank_group(group_teams, group_results)

        result[group_code] = [
            {
                "position": i + 1,
                "team_id": row.team_id,
                "team_name": team_names.get(row.team_id, row.team_id),
                "played": row.played,
                "won": row.won,
                "drawn": row.drawn,
                "lost": row.lost,
                "goals_for": row.goals_for,
                "goals_against": row.goals_against,
                "goal_difference": row.goal_difference,
                "points": row.points,
                "tiebreak_uncertain": row.tiebreak_uncertain,
            }
            for i, row in enumerate(table)
        ]

    return result


def get_third_placed_ranking(session: Session) -> dict[str, Any]:
    """Rank third-placed teams across groups."""
    standings = get_current_standings(session)
    third_placed = [group[2] for group in standings.values() if len(group) >= 3]

    from app.domain.standings import StandingRow
    rows = [
        StandingRow(
            team_id=t["team_id"],
            played=t["played"],
            won=t["won"],
            drawn=t["drawn"],
            lost=t["lost"],
            goals_for=t["goals_for"],
            goals_against=t["goals_against"],
            points=t["points"],
        )
        for t in third_placed
    ]

    if len(rows) == 12:
        ranking = rank_third_placed(rows)
        return {
            "qualified": [{"team_id": r.team_id, "points": r.points, "gd": r.goal_difference} for r in ranking.qualified],
            "eliminated": [{"team_id": r.team_id, "points": r.points, "gd": r.goal_difference} for r in ranking.eliminated],
        }
    return {"qualified": [], "eliminated": []}


def get_group_context_for_match(session: Session, match: Match) -> str | None:
    """Build a group context string for AI prompt."""
    if not match.group_code:
        return None

    standings = get_current_standings(session)
    group_data = standings.get(match.group_code)
    if not group_data:
        return None

    lines = [f"Group {match.group_code} Standings:"]
    for t in group_data:
        marker = ""
        if t["team_id"] == match.home_team_id:
            marker = " [HOME]"
        elif t["team_id"] == match.away_team_id:
            marker = " [AWAY]"
        lines.append(
            f"  {t['position']}. {t['team_name']}{marker}: "
            f"{t['points']}pts, GF={t['goals_for']}, GA={t['goals_against']}, GD={t['goal_difference']}"
        )

    lines.append(f"\nTop 2 qualify, 3rd place may qualify as best third-placed team.")
    return "\n".join(lines)
