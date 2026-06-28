from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Match, Team
from app.tournament.bracket import get_knockout_matchups
from app.tournament.rules import STAGE_ORDER
from app.tournament.standings import get_current_standings, get_third_placed_ranking


ROOT = Path(__file__).resolve().parents[3]
KNOCKOUT_SEED_PATH = ROOT / "data" / "seed" / "world-cup-2026-knockout.json"
_TIME_PATTERN = re.compile(r"^(\d{2}):(\d{2}) UTC([+-]\d{1,2})$")
_BRACKET_RESULT_REF = re.compile(r"^[WL]\d{2,3}$")
_NON_GROUP_STAGES = tuple(stage for stage in STAGE_ORDER if stage != "group")

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnockoutSeedMatch:
    match_number: int
    stage: str
    round_name: str
    date: str
    time: str
    venue: str
    home_source: str
    away_source: str

    @property
    def kickoff(self) -> datetime:
        return _parse_kickoff(self.date, self.time)

    @property
    def match_id(self) -> str:
        return match_id_for_number(self.match_number)


def match_id_for_number(match_number: int) -> str:
    return f"2026-KO-{match_number:03d}"


def load_knockout_seed(path: Path | None = None) -> list[KnockoutSeedMatch]:
    raw = json.loads((path or KNOCKOUT_SEED_PATH).read_text(encoding="utf-8"))
    return [KnockoutSeedMatch(**item) for item in raw["matches"]]


def ensure_knockout_placeholders(session: Session) -> dict[str, int]:
    created = 0
    updated = 0
    links = _build_match_links()

    for seed_match in load_knockout_seed():
        match = session.get(Match, seed_match.match_id)
        if match is None:
            match = Match(
                id=seed_match.match_id,
                group_code=None,
                home_team_id=None,
                away_team_id=None,
                kickoff=seed_match.kickoff,
                venue=seed_match.venue,
                status="scheduled",
                source="tournament",
                source_match_id=seed_match.match_id,
                source_updated_at=datetime.now(timezone.utc),
                stage=seed_match.stage,
                round_name=seed_match.round_name,
                bracket_position=seed_match.match_number,
                home_team_source=seed_match.home_source,
                away_team_source=seed_match.away_source,
                winner_to_match_id=links[seed_match.match_number]["winner_to_match_id"],
                loser_to_match_id=links[seed_match.match_number]["loser_to_match_id"],
                is_placeholder_match=True,
            )
            session.add(match)
            created += 1
            continue

        changed = False
        for field, value in (
            ("kickoff", seed_match.kickoff),
            ("venue", seed_match.venue),
            ("stage", seed_match.stage),
            ("round_name", seed_match.round_name),
            ("bracket_position", seed_match.match_number),
            ("home_team_source", seed_match.home_source),
            ("away_team_source", seed_match.away_source),
            ("winner_to_match_id", links[seed_match.match_number]["winner_to_match_id"]),
            ("loser_to_match_id", links[seed_match.match_number]["loser_to_match_id"]),
            ("is_placeholder_match", True),
        ):
            if getattr(match, field) != value:
                setattr(match, field, value)
                changed = True
        if match.source == "tournament" and match.source_match_id != seed_match.match_id:
            match.source_match_id = seed_match.match_id
            changed = True
        if changed:
            updated += 1

    session.flush()
    return {"created": created, "updated": updated}


def sync_knockout_state(session: Session) -> dict[str, int]:
    outcome = ensure_knockout_placeholders(session)
    outcome["round_of_32_resolved"] = populate_round_of_32_from_standings(session)
    outcome["advanced_slots"] = propagate_knockout_advancement(session)
    session.flush()
    return outcome


def populate_round_of_32_from_standings(session: Session) -> int:
    standings = get_current_standings(session)
    third_placed = get_third_placed_ranking(session)
    matchups = get_knockout_matchups(standings, third_placed)
    updates = 0
    for matchup in matchups:
        match = session.get(Match, match_id_for_number(matchup["match_number"]))
        if match is None:
            continue
        home_id = _team_id(matchup.get("home_team"))
        away_id = _team_id(matchup.get("away_team"))
        if match.home_team_id != home_id:
            match.home_team_id = home_id
            updates += 1
        if match.away_team_id != away_id:
            match.away_team_id = away_id
            updates += 1
        placeholder = not (home_id and away_id)
        if match.is_placeholder_match != placeholder:
            match.is_placeholder_match = placeholder
            updates += 1
    return updates


def propagate_knockout_advancement(session: Session) -> int:
    knockout_matches = list(session.scalars(
        select(Match)
        .where(Match.stage.in_(_NON_GROUP_STAGES))
        .order_by(Match.bracket_position.asc(), Match.id.asc())
    ))
    by_id = {match.id: match for match in knockout_matches}
    updated = 0

    for match in knockout_matches:
        winner_id, loser_id = resolve_knockout_outcome(match)
        if winner_id is None and loser_id is None:
            # Distinguish "not yet played" (expected) from "played but
            # unresolved" (a data-quality bug — typically a level score
            # where neither home_advance nor away_advance was synced).
            # The latter used to silently block bracket progression; we
            # now log a warning so operators can repair the source data.
            if (
                match.status == "final"
                and match.home_team_id is not None
                and match.away_team_id is not None
                and match.home_score is not None
                and match.away_score is not None
            ):
                _logger.warning(
                    "knockout match %s finished but outcome unresolved: "
                    "score=%s-%s, home_advance=%s, away_advance=%s — "
                    "downstream bracket cannot progress until advance flag is set",
                    match.id, match.home_score, match.away_score,
                    match.home_advance, match.away_advance,
                )
            continue

        if match.winner_to_match_id:
            target = by_id.get(match.winner_to_match_id) or session.get(Match, match.winner_to_match_id)
            updated += _assign_progression_slot(target, match.bracket_position, winner_id, winner=True)

        if match.loser_to_match_id:
            target = by_id.get(match.loser_to_match_id) or session.get(Match, match.loser_to_match_id)
            updated += _assign_progression_slot(target, match.bracket_position, loser_id, winner=False)

    return updated


def get_knockout_bracket_payload(session: Session) -> dict[str, list[dict[str, Any]]]:
    sync_knockout_state(session)
    teams = {
        team.id: team
        for team in session.scalars(select(Team))
    }
    grouped: dict[str, list[dict[str, Any]]] = {stage: [] for stage in _NON_GROUP_STAGES}

    matches = list(session.scalars(
        select(Match)
        .where(Match.stage.in_(_NON_GROUP_STAGES))
        .order_by(Match.bracket_position.asc(), Match.id.asc())
    ))

    for match in matches:
        grouped.setdefault(match.stage, []).append({
            "id": match.id,
            "match_number": match.bracket_position,
            "match_position": match.bracket_position,
            "stage": match.stage,
            "round_name": match.round_name,
            "status": match.status,
            "kickoff": match.kickoff.isoformat() if match.kickoff else None,
            "venue": match.venue,
            "home_source": match.home_team_source,
            "away_source": match.away_team_source,
            "home_team": _serialize_team(teams.get(match.home_team_id)),
            "away_team": _serialize_team(teams.get(match.away_team_id)),
            "home_score": match.home_score,
            "away_score": match.away_score,
            "home_advance": match.home_advance,
            "away_advance": match.away_advance,
            "went_to_extra_time": match.went_to_extra_time,
            "went_to_penalties": match.went_to_penalties,
            "home_penalty_score": match.home_penalty_score,
            "away_penalty_score": match.away_penalty_score,
            "winner_to_match_id": match.winner_to_match_id,
            "loser_to_match_id": match.loser_to_match_id,
            "is_placeholder_match": match.is_placeholder_match,
        })

    return grouped


def resolve_knockout_outcome(match: Match) -> tuple[str | None, str | None]:
    if match.status != "final":
        return None, None
    if match.home_team_id is None or match.away_team_id is None:
        return None, None
    if match.home_score is None or match.away_score is None:
        return None, None
    if match.home_score > match.away_score:
        return match.home_team_id, match.away_team_id
    if match.away_score > match.home_score:
        return match.away_team_id, match.home_team_id
    # Scores are level — knockout matches must still produce a winner via
    # extra time / penalties, encoded in home_advance / away_advance.
    if match.home_advance is True and match.away_advance is not True:
        return match.home_team_id, match.away_team_id
    if match.away_advance is True and match.home_advance is not True:
        return match.away_team_id, match.home_team_id
    if match.home_advance is True and match.away_advance is True:
        # Conflicting flags — should never happen but log so the source
        # data can be repaired. Default to home_advance to keep bracket
        # progressing rather than silently stalling.
        _logger.error(
            "knockout match %s has both home_advance and away_advance set "
            "with level score %s-%s; defaulting to home_advance",
            match.id, match.home_score, match.away_score,
        )
        return match.home_team_id, match.away_team_id
    return None, None


def _serialize_team(team: Team | None) -> dict[str, str] | None:
    if team is None:
        return None
    return {
        "team_id": team.id,
        "team_name": team.short_name,
        "code": team.code,
    }


def _team_id(team_payload: dict[str, Any] | None) -> str | None:
    if not team_payload:
        return None
    return team_payload.get("team_id")


def _assign_progression_slot(target: Match | None, match_number: int | None, team_id: str | None, *, winner: bool) -> int:
    if target is None or match_number is None or team_id is None:
        return 0
    ref = f"{'W' if winner else 'L'}{match_number}"
    changed = 0
    if target.home_team_source == ref and target.home_team_id != team_id:
        target.home_team_id = team_id
        changed += 1
    if target.away_team_source == ref and target.away_team_id != team_id:
        target.away_team_id = team_id
        changed += 1
    if changed:
        target.is_placeholder_match = not (target.home_team_id and target.away_team_id)
    return changed


def _build_match_links() -> dict[int, dict[str, str | None]]:
    links: dict[int, dict[str, str | None]] = {}
    for match_number in range(73, 105):
        links[match_number] = {"winner_to_match_id": None, "loser_to_match_id": None}

    seed_matches = load_knockout_seed()
    for seed_match in seed_matches:
        for source in (seed_match.home_source, seed_match.away_source):
            if not _BRACKET_RESULT_REF.fullmatch(source):
                continue
            origin_match = int(source[1:])
            key = "winner_to_match_id" if source[0] == "W" else "loser_to_match_id"
            links[origin_match][key] = seed_match.match_id
    return links


def _parse_kickoff(date_value: str, time_value: str) -> datetime:
    match = _TIME_PATTERN.fullmatch(time_value)
    if not match:
        raise ValueError(f"unsupported knockout time: {time_value}")
    hour, minute, offset = map(int, match.groups())
    local_timezone = timezone(timedelta(hours=offset))
    return datetime.fromisoformat(f"{date_value}T{hour:02d}:{minute:02d}:00").replace(
        tzinfo=local_timezone
    ).astimezone(timezone.utc)
