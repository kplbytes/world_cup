from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from collections import defaultdict
import time

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from starlette.requests import Request
from starlette.responses import Response

from app.api.routes import router
from app.api.routes.dashboard_routes import _build_providers
from app.config import PROJECT_ROOT, settings
from app.db import create_database, session_scope
from app.logging_config import setup_logging

import logging
logger = logging.getLogger(__name__)
from app.middleware import AccessLogMiddleware, RequestIdMiddleware
from app.models import DashboardRevision, Match, Team, TeamProfile
from app.schemas import TournamentPayload
from app.services.recompute import recompute_all
from app.services.refresh import refresh_tournament
from app.services.seed import seed_ratings, seed_team_aliases, seed_tournament
from app.services.snapshots import lock_due_predictions, repair_invalid_prediction_locks
from app.tournament.knockout import sync_knockout_state

# Initialize logging before anything else
setup_logging()

# ---------------------------------------------------------------------------
# In-memory rate limiter (per-IP, sliding window)
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
_RATE_WINDOW = 60  # seconds
_RATE_CLEANUP_THRESHOLD = 120  # seconds - remove IPs with no requests in this period

# Module-level reference to the scheduler, set during create_app()
_scheduler: BackgroundScheduler | None = None


def _check_rate_limit(client_ip: str, method: str) -> int | None:
    """Return None if allowed, or the retry-after seconds if rate limited."""
    if method == "GET":
        limit = 120
    else:
        limit = 60
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    # Periodic cleanup: remove entries with no requests in the last 120 seconds
    cleanup_threshold = now - _RATE_CLEANUP_THRESHOLD
    stale_ips = [ip for ip, timestamps in _rate_limit_store.items() if not timestamps or timestamps[-1] < cleanup_threshold]
    for ip in stale_ips:
        del _rate_limit_store[ip]
    timestamps = _rate_limit_store[client_ip]
    # Prune old entries
    _rate_limit_store[client_ip] = timestamps = [
        t for t in timestamps if t > window_start
    ]
    if len(timestamps) >= limit:
        return int(timestamps[0] + _RATE_WINDOW - now) + 1
    timestamps.append(now)
    return None


class RateLimitMiddleware:
    """Simple per-IP rate limiter using an in-memory sliding window."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        client_ip = request.client.host if request.client else "unknown"
        retry_after = _check_rate_limit(client_ip, request.method)
        if retry_after is not None:
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class ApiKeyMiddleware:
    """API key authentication for write endpoints (POST/DELETE/PATCH).

    If ``settings.admin_api_key`` is empty or not configured, auth is skipped
    (backward compatible).  GET endpoints are always open.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if method not in ("POST", "DELETE", "PATCH"):
            await self.app(scope, receive, send)
            return
        api_key = settings.admin_api_key
        if not api_key:
            # No key configured → auth disabled
            await self.app(scope, receive, send)
            return
        # Extract X-API-Key header from raw headers
        headers = dict(scope.get("headers", []))
        provided = headers.get(b"x-api-key", b"").decode("utf-8", errors="replace")
        if provided != api_key:
            response = JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing API key"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


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


def _repair_stuck_workflows(session: Session) -> None:
    """Mark any 'running' workflows from a previous unclean shutdown as failed."""
    now = datetime.now(timezone.utc)
    stuck_steps = session.execute(
        text("UPDATE workflow_steps SET status='failed', error_message='Server restarted - step interrupted', finished_at=:now WHERE status='running'"),
        {"now": now.isoformat()},
    )
    stuck_runs = session.execute(
        text("UPDATE workflow_runs SET status='failed', error_message='Server restarted - workflow interrupted', finished_at=:now WHERE status='running'"),
        {"now": now.isoformat()},
    )
    total = (stuck_steps.rowcount or 0) + (stuck_runs.rowcount or 0)
    if total:
        logger.warning("repaired %d stuck workflow records from previous session", total)


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
        sync_knockout_state(session)
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

        # Repair stuck workflows from previous unclean shutdown
        _repair_stuck_workflows(session)


def create_app(start_background: bool = True) -> FastAPI:
    global _scheduler
    scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler = scheduler

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

    def scheduled_auto_ai() -> None:
        """Optionally run pre-match AI workflow from the backend scheduler."""
        if settings.ai_run_mode != "auto" or not settings.enable_ai_prediction:
            return

        from app.workflows import service as workflow_service

        status = workflow_service.get_workflow_status()
        ai_button = (status.get("button_states") or {}).get("ai_prediction") or {}
        if not ai_button.get("enabled"):
            logger.info("scheduled_auto_ai skipped: %s", ai_button.get("reason", "disabled"))
            return

        run_id = workflow_service.run_pre_match_workflow(
            hours=settings.workflow_default_hours,
            limit=settings.workflow_default_limit,
            with_ai=True,
            with_ensemble=True,
            only_missing=True,
            trigger_source="auto_scheduler",
        )
        if run_id > 0:
            logger.info("scheduled_auto_ai started pre_match workflow run_id=%s", run_id)
        else:
            logger.info("scheduled_auto_ai skipped: workflow lock unavailable")

    def scheduled_maintenance() -> None:
        """Periodically clean up old non-revision-bound data and prune stale revisions."""
        from datetime import timedelta

        with session_scope() as session:
            cutoff = datetime.now(timezone.utc) - timedelta(days=7)
            cutoff_str = cutoff.isoformat()

            # data_snapshots: keep 7 days for checksum dedup
            r1 = session.execute(
                text("DELETE FROM data_snapshots WHERE fetched_at < :cutoff"),
                {"cutoff": cutoff_str},
            )
            # match_intelligence: keep 7 days
            r2 = session.execute(
                text("DELETE FROM match_intelligence WHERE fetched_at < :cutoff"),
                {"cutoff": cutoff_str},
            )
            total = (r1.rowcount or 0) + (r2.rowcount or 0)
            if total:
                logger.info("maintenance: pruned %d old records (data_snapshots=%d, match_intelligence=%d)",
                           total, r1.rowcount or 0, r2.rowcount or 0)

            # P2-H: prune old dashboard revisions' non-scoring data so the
            # database doesn't grow unboundedly over a long tournament.
            # Scoring history (match_predictions, prediction_snapshots,
            # model_scores) is preserved by _prune_old_revisions.
            try:
                from app.services.recompute import _prune_old_revisions
                _prune_old_revisions(session, keep=5)
                session.commit()
            except Exception as exc:
                logger.warning("maintenance: prune_old_revisions failed: %s", exc)

            # P2-H: invalidate caches after maintenance so the next read
            # picks up the pruned state.
            try:
                from app.services.dashboard import invalidate_dashboard_caches
                invalidate_dashboard_caches()
            except Exception:
                pass

    def scheduled_post_match() -> None:
        """P4-D: Automatically run post-match scoring for finished matches.

        Checks if any matches transitioned to 'final' since the last run.
        If so, triggers the post_match workflow (recompute + score) so the
        model self-corrects without manual intervention. Runs every 30 min.
        """
        from app.workflows import service as workflow_service

        status = workflow_service.get_workflow_status()
        post_match_status = (status.get("button_states") or {}).get("post_match") or {}
        if not post_match_status.get("enabled"):
            return  # no finished matches to score, or a workflow is running

        run_id = workflow_service.run_post_match_workflow(
            since_hours=6,
            trigger_source="auto_scheduler",
        )
        if run_id > 0:
            logger.info("scheduled_post_match started workflow run_id=%s", run_id)
        else:
            logger.info("scheduled_post_match skipped: workflow lock unavailable")

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if start_background:
            initialize_database()
            if settings.enable_scheduled_refresh:
                scheduler.add_job(
                    scheduled_refresh,
                    "interval",
                    minutes=settings.refresh_interval_minutes,
                    id="world-cup-refresh",
                    max_instances=1,
                    coalesce=True,
                )
            if settings.ai_run_mode == "auto" and settings.enable_ai_prediction:
                auto_ai_offset = max(1, settings.refresh_interval_minutes // 2)
                scheduler.add_job(
                    scheduled_auto_ai,
                    "interval",
                    minutes=settings.refresh_interval_minutes,
                    id="world-cup-auto-ai",
                    max_instances=1,
                    coalesce=True,
                    next_run_time=datetime.now(timezone.utc) + timedelta(minutes=auto_ai_offset),
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
            scheduler.add_job(
                scheduled_maintenance,
                "interval",
                hours=6,
                id="world-cup-maintenance",
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            # P4-D: automatic post-match scoring so the model self-corrects
            # without manual button clicks. Runs every 30 min; the function
            # itself short-circuits when there are no finished matches to score.
            scheduler.add_job(
                scheduled_post_match,
                "interval",
                minutes=30,
                id="world-cup-post-match",
                max_instances=1,
                coalesce=True,
                next_run_time=datetime.now(timezone.utc) + timedelta(minutes=10),
            )
            scheduler.start()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)
        # Close AI provider clients on shutdown
        from app.ai.provider_registry import close_all_ai_providers
        await close_all_ai_providers()

    app = FastAPI(title="2026 World Cup Predictor", lifespan=lifespan)

    # CORS middleware (origins configurable via CORS_ALLOWED_ORIGINS env var)
    cors_origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add middleware (order matters: outermost first)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(ApiKeyMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(AccessLogMiddleware)

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
            resolved_path = file_path.resolve()
            if not resolved_path.is_relative_to(frontend_dist):
                return Response(status_code=404)
            if resolved_path.is_file():
                return FileResponse(resolved_path)
            return FileResponse(index_html)

    return app


app = create_app()
