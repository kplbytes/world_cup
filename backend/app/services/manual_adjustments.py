from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ManualAdjustment, Match


@dataclass(frozen=True)
class MatchAdjustmentContext:
    home_attack_adjustment: float = 0.0
    home_defense_adjustment: float = 0.0
    away_attack_adjustment: float = 0.0
    away_defense_adjustment: float = 0.0


def list_manual_adjustments(
    session: Session,
    match_id: str | None = None,
) -> list[ManualAdjustment]:
    stmt = select(ManualAdjustment).order_by(
        ManualAdjustment.created_at.desc(),
        ManualAdjustment.id.desc(),
    )
    if match_id is not None:
        stmt = stmt.where(ManualAdjustment.match_id == match_id)
    return list(session.scalars(stmt))


def adjustments_by_match(session: Session) -> dict[str, list[ManualAdjustment]]:
    grouped: dict[str, list[ManualAdjustment]] = defaultdict(list)
    for adjustment in list_manual_adjustments(session):
        grouped[adjustment.match_id].append(adjustment)
    return grouped


def build_adjustment_context(
    match: Match,
    adjustments: list[ManualAdjustment],
) -> MatchAdjustmentContext:
    home_attack = 0.0
    home_defense = 0.0
    away_attack = 0.0
    away_defense = 0.0

    for adjustment in adjustments:
        if adjustment.affected_team_id == match.home_team_id:
            home_attack += adjustment.attack_delta
            home_defense += adjustment.defense_delta
        elif adjustment.affected_team_id == match.away_team_id:
            away_attack += adjustment.attack_delta
            away_defense += adjustment.defense_delta

    return MatchAdjustmentContext(
        home_attack_adjustment=home_attack,
        home_defense_adjustment=home_defense,
        away_attack_adjustment=away_attack,
        away_defense_adjustment=away_defense,
    )


def serialize_adjustment(
    adjustment: ManualAdjustment,
    team_names: dict[str, str],
) -> dict:
    return {
        "id": adjustment.id,
        "match_id": adjustment.match_id,
        "adjustment_type": adjustment.adjustment_type,
        "affected_team_id": adjustment.affected_team_id,
        "affected_team_name": team_names.get(
            adjustment.affected_team_id,
            adjustment.affected_team_id,
        ),
        "attack_delta": adjustment.attack_delta,
        "defense_delta": adjustment.defense_delta,
        "confidence": adjustment.confidence,
        "note": adjustment.note,
        "created_by": adjustment.created_by,
        "created_at": adjustment.created_at.isoformat(),
    }
