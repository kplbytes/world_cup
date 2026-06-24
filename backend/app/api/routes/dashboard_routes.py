from fastapi import APIRouter, HTTPException

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import DashboardRevision
from app.providers.football_data import FootballDataProvider, is_configured as fd_is_configured
from app.providers.openfootball import OpenFootballProvider
from app.providers.worldcup26 import WorldCup26Provider
from app.services.dashboard import build_dashboard, build_decision, build_match_detail, build_team_detail, list_data_sources, list_intelligence_providers, list_sync_runs
from app.services.refresh import refresh_tournament


router = APIRouter()


def _build_providers():
    """Build the list of active data providers for manual refresh."""
    providers = [OpenFootballProvider.from_remote()]
    if fd_is_configured():
        providers.append(FootballDataProvider())
    providers.append(WorldCup26Provider())
    return providers


@router.get("/health")
def health():
    from app.ai.model_registry import get_provider_config, list_enabled_models
    from app.main import _scheduler

    # Database check - lightweight query instead of full build_dashboard
    with session_scope() as session:
        active = session.scalar(
            select(DashboardRevision.id)
            .where(DashboardRevision.active.is_(True))
            .limit(1)
        )
        revision_id = active

    # AI providers availability
    ai_models = list_enabled_models()
    ai_available = False
    for model in ai_models:
        provider_config = get_provider_config(model.provider_name)
        if provider_config:
            from app.config import settings as _settings
            attr = provider_config.api_key_env.lower()
            key = getattr(_settings, attr, "")
            if len(key) > 0:
                ai_available = True
                break

    # APScheduler running status
    scheduler_running = _scheduler is not None and _scheduler.running

    # Last successful workflow run time
    last_successful_run = None
    with session_scope() as session:
        from sqlalchemy import select as _select
        from app.models import SyncRun
        row = session.scalar(
            _select(SyncRun)
            .where(SyncRun.status == "success")
            .order_by(SyncRun.id.desc())
            .limit(1)
        )
        if row and row.finished_at:
            last_successful_run = row.finished_at.isoformat()

    dependencies = {
        "database": "ok" if revision_id is not None else "no_revision",
        "ai_providers": "available" if ai_available else "no_api_keys",
        "apscheduler": "running" if scheduler_running else "stopped",
        "last_successful_run": last_successful_run,
    }

    overall = "ok" if (revision_id is not None and scheduler_running) else "degraded"
    return {"status": overall, "revision_id": revision_id, "dependencies": dependencies}


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
        result = build_match_detail(session, match_id)
        if result is None:
            raise HTTPException(status_code=404, detail="match not found")
        return result


@router.get("/teams/{team_id}")
def team_detail(team_id: str):
    with session_scope() as session:
        result = build_team_detail(session, team_id)
        if result is None:
            raise HTTPException(status_code=404, detail="team not found")
        return result


@router.get("/data-sources")
def data_sources():
    with session_scope() as session:
        return {
            "data_sources": list_data_sources(session),
            "intelligence_providers": list_intelligence_providers(session),
        }


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


@router.get("/decision")
def decision():
    with session_scope() as session:
        return build_decision(session)
