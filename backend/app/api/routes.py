from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import ManualAdjustment, Match, ModelScore, Team
from app.providers.football_data import FootballDataProvider, is_configured as fd_is_configured
from app.providers.openfootball import OpenFootballProvider
from app.schemas import ManualAdjustmentCreate
from app.services.dashboard import build_dashboard, build_decision, list_data_sources, list_sync_runs
from app.services.localization import localized_team_names
from app.services.manual_adjustments import list_manual_adjustments, serialize_adjustment
from app.services.recompute import recompute_all
from app.services.refresh import refresh_tournament
from app.services.scoring import model_score_payload, save_model_score, score_model


router = APIRouter(prefix="/api")


def _build_providers():
    """Build the list of active data providers for manual refresh."""
    providers = [OpenFootballProvider.from_remote()]
    if fd_is_configured():
        providers.append(FootballDataProvider())
    return providers


@router.get("/health")
def health():
    with session_scope() as session:
        try:
            dashboard = build_dashboard(session)
            revision_id = dashboard["revision"]["id"]
        except LookupError:
            revision_id = None
    return {"status": "ok", "revision_id": revision_id}


@router.get("/dashboard")
def dashboard():
    with session_scope() as session:
        try:
            return build_dashboard(session)
        except LookupError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/groups/{group_code}")
def group_detail(group_code: str):
    group_code = group_code.upper()
    if group_code not in "ABCDEFGHIJKL":
        raise HTTPException(status_code=404, detail="group not found")
    with session_scope() as session:
        data = build_dashboard(session)
        return next(group for group in data["groups"] if group["code"] == group_code)


@router.get("/matches")
def matches(status: str | None = None):
    with session_scope() as session:
        data = build_dashboard(session)
        rows = [match for group in data["groups"] for match in group["matches"]]
        return [match for match in rows if status is None or match["status"] == status]


@router.get("/matches/{match_id}")
def match_detail(match_id: str):
    with session_scope() as session:
        data = build_dashboard(session)
        for group in data["groups"]:
            for match in group["matches"]:
                if match["id"] == match_id:
                    return match
    raise HTTPException(status_code=404, detail="match not found")


@router.get("/teams/{team_id}")
def team_detail(team_id: str):
    with session_scope() as session:
        data = build_dashboard(session)
        for group in data["groups"]:
            for team in group["teams"]:
                if team["id"] == team_id:
                    team_matches = [
                        match
                        for match in group["matches"]
                        if team_id in (match["home_team"]["id"], match["away_team"]["id"])
                    ]
                    return {**team, "group_code": group["code"], "matches": team_matches}
    raise HTTPException(status_code=404, detail="team not found")


@router.get("/data-sources")
def data_sources():
    with session_scope() as session:
        return list_data_sources(session)


@router.get("/sync-runs")
def sync_runs():
    with session_scope() as session:
        return list_sync_runs(session)


@router.post("/refresh")
def refresh():
    with session_scope() as session:
        outcome = refresh_tournament(
            session,
            providers=_build_providers(),
            iterations=settings.simulation_iterations,
            seed=settings.simulation_seed,
        )
        if outcome.status == "failed":
            raise HTTPException(status_code=502, detail={"errors": outcome.errors})
        return {
            "status": outcome.status,
            "finalized_matches": outcome.finalized_matches,
            "updated_matches": outcome.updated_matches,
            "warnings": outcome.warnings,
            "revision_id": outcome.revision_id,
        }


@router.get("/model-score")
def model_score():
    with session_scope() as session:
        return model_score_payload(session)


@router.get("/decision")
def decision():
    with session_scope() as session:
        return build_decision(session)


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
