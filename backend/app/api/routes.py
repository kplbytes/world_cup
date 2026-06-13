from fastapi import APIRouter, HTTPException

from app.config import settings
from app.db import session_scope
from app.providers.football_data import FootballDataProvider, is_configured as fd_is_configured
from app.providers.openfootball import OpenFootballProvider
from app.models import ModelScore
from app.services.dashboard import build_dashboard, build_decision, list_data_sources, list_sync_runs
from app.services.refresh import refresh_tournament


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
        from sqlalchemy import select
        row = session.scalar(
            select(ModelScore).order_by(ModelScore.id.desc()).limit(1)
        )
        if row is None:
            return {"matches_scored": 0, "per_match": []}
        return {
            "id": row.id,
            "revision_id": row.revision_id,
            "matches_scored": row.matches_scored,
            "brier_score": row.brier_score,
            "log_loss": row.log_loss,
            "outcome_hit_rate": row.outcome_hit_rate,
            "top_score_hit_rate": row.top_score_hit_rate,
            "xg_mae": row.xg_mae,
            "per_match": row.per_match,
            "created_at": row.created_at.isoformat(),
        }


@router.get("/decision")
def decision():
    with session_scope() as session:
        return build_decision(session)
