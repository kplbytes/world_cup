from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.routes import router
from app.api.routes.dashboard_routes import _build_providers
from app.config import PROJECT_ROOT, settings
from app.db import create_database, session_scope
from app.models import DashboardRevision, Match, Team, TeamProfile
from app.schemas import TournamentPayload
from app.services.recompute import recompute_all
from app.services.refresh import refresh_tournament
from app.services.seed import seed_ratings, seed_team_aliases, seed_tournament
from app.services.snapshots import lock_due_predictions, repair_invalid_prediction_locks


def _is_live_window(session: Session, now: datetime | None = None) -> bool:
    """Check whether any match is currently live or kicked off within the last 3 hours."""
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=3)
    recent_match = session.scalar(
        select(Match.id)
        .where(
            Match.status != "final",
            Match.kickoff >= window_start,
            Match.kickoff <= now,
        )
        .limit(1)
    )
    return recent_match is not None


def initialize_database() -> None:
    create_database()
    with session_scope() as session:
        team_count = session.scalar(select(func.count(Team.id)))
        if team_count == 0:
            seed_path = PROJECT_ROOT / "data" / "seed" / "world-cup-2026.json"
            payload = TournamentPayload.model_validate_json(seed_path.read_text(encoding="utf-8"))
            seed_tournament(session, payload)
        seed_ratings(session, PROJECT_ROOT / "data" / "seed" / "elo-ratings-2026.json")
        seed_team_aliases(
            session, PROJECT_ROOT / "data" / "seed" / "sporttery-team-aliases.json"
        )
        profile_count = session.scalar(select(func.count(TeamProfile.id))) or 0
        if profile_count == 0:
            from app.team_profiles.service import rebuild_team_profiles
            rebuild_team_profiles(session, use_seed=True)
        active = session.scalar(
            select(DashboardRevision.id).where(DashboardRevision.active.is_(True)).limit(1)
        )
        if active is None:
            recompute_all(
                session,
                iterations=settings.simulation_iterations,
                seed=settings.simulation_seed,
            )
        repair_invalid_prediction_locks(session)
        lock_due_predictions(session)


def create_app(start_background: bool = True) -> FastAPI:
    scheduler = BackgroundScheduler(timezone="UTC")

    def scheduled_refresh() -> None:
        with session_scope() as session:
            refresh_tournament(
                session,
                providers=_build_providers(),
                iterations=settings.simulation_iterations,
                seed=settings.simulation_seed,
            )
            live = _is_live_window(session)
        target_interval = (
            settings.live_refresh_interval_minutes if live
            else settings.refresh_interval_minutes
        )
        job = scheduler.get_job("world-cup-refresh")
        if job and job.trigger.interval.total_seconds() != target_interval * 60:
            scheduler.reschedule_job(
                "world-cup-refresh",
                trigger="interval",
                minutes=target_interval,
            )

    def scheduled_snapshot_lock() -> None:
        with session_scope() as session:
            lock_due_predictions(session)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background:
            initialize_database()
            scheduler.add_job(
                scheduled_refresh,
                "interval",
                minutes=settings.refresh_interval_minutes,
                id="world-cup-refresh",
                max_instances=1,
                coalesce=True,
            )
            scheduler.add_job(
                scheduled_snapshot_lock,
                "interval",
                minutes=settings.snapshot_lock_interval_minutes,
                id="world-cup-snapshot-lock",
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(timezone.utc),
            )
            scheduler.start()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)
        # Close AI provider clients on shutdown
        from app.ai.provider_registry import close_all_ai_providers
        await close_all_ai_providers()

    app = FastAPI(title="2026 World Cup Predictor", lifespan=lifespan)
    app.include_router(router)
    frontend_dist = PROJECT_ROOT / "frontend" / "dist"
    if frontend_dist.exists():
        assets_dir = frontend_dist / "assets"
        if assets_dir.exists():
            app.mount("/assets", StaticFiles(directory=assets_dir), name="static-assets")

        index_html = frontend_dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa_fallback(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404, detail="not found")
            file_path = frontend_dist / full_path
            if file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(index_html)

    return app


app = create_app()
