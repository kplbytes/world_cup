from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import ManualAdjustment, Match, Team
from app.schemas import ManualAdjustmentCreate
from app.services.localization import localized_team_names
from app.services.manual_adjustments import list_manual_adjustments, serialize_adjustment
from app.services.recompute import recompute_all
from app.services.scoring import save_model_score, score_model


router = APIRouter()


@router.get("/manual-adjustments")
def manual_adjustments(match_id: str | None = None):
    with session_scope() as session:
        display_names = localized_team_names(session, list(session.scalars(select(Team))))
        return [
            serialize_adjustment(adjustment, display_names)
            for adjustment in list_manual_adjustments(session, match_id=match_id)
        ]


@router.post("/manual-adjustments")
def create_manual_adjustment(payload: ManualAdjustmentCreate):
    with session_scope() as session:
        match = session.get(Match, payload.match_id)
        if match is None:
            raise HTTPException(status_code=404, detail="match not found")
        if match.status == "final":
            raise HTTPException(status_code=400, detail="final matches cannot be adjusted")
        if payload.affected_team_id not in (match.home_team_id, match.away_team_id):
            raise HTTPException(status_code=400, detail="affected team must belong to the match")

        adjustment = ManualAdjustment(
            match_id=payload.match_id,
            adjustment_type=payload.adjustment_type,
            affected_team_id=payload.affected_team_id,
            attack_delta=payload.attack_delta,
            defense_delta=payload.defense_delta,
            confidence=payload.confidence,
            note=payload.note,
            created_by=payload.created_by,
        )
        session.add(adjustment)
        session.flush()
        revision = recompute_all(
            session,
            iterations=settings.simulation_iterations,
            seed=settings.simulation_seed,
        )
        save_model_score(session, score_model(session), revision.id)
        display_names = localized_team_names(session, list(session.scalars(select(Team))))
        return {
            "adjustment": serialize_adjustment(adjustment, display_names),
            "revision_id": revision.id,
        }


@router.delete("/manual-adjustments/{adjustment_id}")
def delete_manual_adjustment(adjustment_id: int):
    with session_scope() as session:
        adjustment = session.get(ManualAdjustment, adjustment_id)
        if adjustment is None:
            raise HTTPException(status_code=404, detail="adjustment not found")
        session.delete(adjustment)
        revision = recompute_all(
            session,
            iterations=settings.simulation_iterations,
            seed=settings.simulation_seed,
        )
        save_model_score(session, score_model(session), revision.id)
        return {"status": "deleted", "revision_id": revision.id}


@router.get("/accuracy-command-center")
def accuracy_command_center():
    """Unified accuracy command center - overall model assessment."""
    from app.services.accuracy_command import get_accuracy_command_center
    with session_scope() as session:
        return get_accuracy_command_center(session)
