"""Workflow API routes - local workflow center."""

import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import WorkflowRun, WorkflowStep
from app.workflows.schemas import (
    DailyOpenRequest, PreMatchRequest, PostMatchRequest,
    LockRequest, FullWorkflowRequest,
)
from app.workflows import service as workflow_service
from app.workflows.state import is_workflow_running

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/workflows", tags=["workflows"])

# Dedicated executor for workflow background tasks (max 1 thread to serialize workflows)
_workflow_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="workflow-bg")


@router.get("/status")
def workflow_status():
    """Get the current workflow status for the local workflow center."""
    return workflow_service.get_workflow_status()


def _started_response(run_id: int, progress: dict) -> dict:
    return {
        "status": "started",
        "message": "Workflow dispatched to background",
        "run_id": run_id,
        "progress": progress,
    }


def _check_ai_available_for_auto() -> tuple[bool, str]:
    """Check if AI auto-run should proceed for daily-open.

    Only auto-run AI for matches that are:
    - Unstarted and within next 48 hours
    - AI enabled and API key ready
    - No existing valid AI prediction
    - Not exceeding AI_RUN_ALL_MAX_LIMIT
    """
    from app.ai.service import is_ai_enabled
    from app.ai.model_registry import list_enabled_models, list_enabled_providers
    from app.config import settings as _settings

    if not is_ai_enabled():
        return False, "AI 预测未启用"

    # Check if at least one provider has an API key configured
    has_key = False
    missing_providers = []
    for provider_config in list_enabled_providers():
        attr = provider_config.api_key_env.lower()
        key = getattr(_settings, attr, "")
        if key:
            has_key = True
        else:
            missing_providers.append(provider_config.provider_name)

    if not has_key:
        return False, f"未配置 API Key（{', '.join(missing_providers)}）"

    # Check if there are matches needing AI
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import select, func
    from app.models import Match, AIPrediction
    from app.db import session_scope

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=48)

    with session_scope() as session:
        # Count upcoming unstarted matches within 48h
        upcoming = list(session.scalars(
            select(Match)
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
            .where(Match.kickoff <= cutoff)
        ))

        if not upcoming:
            return False, "没有未来 48 小时内的比赛"

        # Count matches that already have valid AI predictions
        needs_ai = 0
        enabled_versions = {m.model_version for m in list_enabled_models()}
        for match in upcoming:
            existing = list(session.scalars(
                select(AIPrediction)
                .where(AIPrediction.match_id == match.id)
                .where(AIPrediction.error_code.is_(None))
                .where(AIPrediction.parsed_home_win.isnot(None))
            ))
            covered_versions = {p.model_version for p in existing}
            if enabled_versions - covered_versions:
                needs_ai += 1

        if needs_ai == 0:
            return False, "所有比赛已有有效 AI 预测"

        if needs_ai > _settings.ai_run_all_max_limit:
            return True, f"有 {needs_ai} 场需 AI（将处理前 {_settings.ai_run_all_max_limit} 场）"

        return True, f"有 {needs_ai} 场比赛需要 AI 预测"


@router.post("/daily-open")
def workflow_daily_open(req: DailyOpenRequest = DailyOpenRequest()):
    """Manually trigger daily open workflow.

    The workflow is executed in a background thread to avoid blocking the request
    handler while recompute (25-40s) runs.
    """
    if is_workflow_running():
        return {"status": "already_running", "message": "A workflow is already running"}

    effective_with_ai = req.with_ai and _check_ai_available_for_auto()[0]

    effective_limit = min(req.limit, settings.ai_run_all_max_limit)
    run_id = workflow_service.start_workflow_run("daily_open", "manual_button", {
        "hours": req.hours,
        "since_hours": req.since_hours,
        "limit": effective_limit,
        "with_ai": effective_with_ai,
        "with_ensemble": req.with_ensemble,
        "auto_lock": req.auto_lock,
        "only_missing": req.only_missing,
    })
    if run_id < 0:
        return {"status": "already_running", "message": "A workflow is already running"}
    progress = workflow_service.get_run_progress(run_id)

    def _run_in_background():
        try:
            workflow_service.execute_daily_open_workflow(
                run_id,
                hours=req.hours,
                since_hours=req.since_hours,
                limit=effective_limit,
                with_ai=effective_with_ai,
                with_ensemble=req.with_ensemble,
                auto_lock=req.auto_lock,
                only_missing=req.only_missing,
                trigger_source="manual_button",
            )
        except Exception:
            logger.exception("daily_open workflow background task failed")

    _workflow_executor.submit(_run_in_background)
    return _started_response(run_id, progress)


@router.post("/pre-match")
def workflow_pre_match(req: PreMatchRequest = PreMatchRequest()):
    """Manually trigger pre-match prediction workflow (background)."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    effective_limit = min(req.limit, settings.ai_run_all_max_limit)
    run_id = workflow_service.start_workflow_run("pre_match", "manual_button", {
        "hours": req.hours,
        "limit": effective_limit,
        "with_ai": req.with_ai,
        "with_ensemble": req.with_ensemble,
        "only_missing": req.only_missing,
    })
    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")
    progress = workflow_service.get_run_progress(run_id)

    def _run_in_background():
        try:
            workflow_service.execute_pre_match_workflow(
                run_id,
                hours=req.hours,
                limit=effective_limit,
                with_ai=req.with_ai,
                with_ensemble=req.with_ensemble,
                only_missing=req.only_missing,
                trigger_source="manual_button",
            )
        except Exception:
            logger.exception("pre_match workflow background task failed")

    _workflow_executor.submit(_run_in_background)
    return _started_response(run_id, progress)


@router.post("/post-match")
def workflow_post_match(req: PostMatchRequest = PostMatchRequest()):
    """Manually trigger post-match review workflow (background)."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")
    run_id = workflow_service.start_workflow_run("post_match", "manual_button", {
        "since_hours": req.since_hours,
    })
    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")
    progress = workflow_service.get_run_progress(run_id)

    def _run_in_background():
        try:
            workflow_service.execute_post_match_workflow(
                run_id,
                since_hours=req.since_hours,
                trigger_source="manual_button",
            )
        except Exception:
            logger.exception("post_match workflow background task failed")

    _workflow_executor.submit(_run_in_background)
    return _started_response(run_id, progress)


@router.post("/lock")
def workflow_lock(req: LockRequest = LockRequest()):
    """Manually trigger pre-match decision snapshot workflow (background)."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")
    run_id = workflow_service.start_workflow_run("lock", "manual_button", {
        "window_hours": req.window_hours,
    })
    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")
    progress = workflow_service.get_run_progress(run_id)

    def _run_in_background():
        try:
            workflow_service.execute_lock_workflow(
                run_id,
                window_hours=req.window_hours,
                trigger_source="manual_button",
            )
        except Exception:
            logger.exception("lock workflow background task failed")

    _workflow_executor.submit(_run_in_background)
    return _started_response(run_id, progress)


@router.post("/full")
def workflow_full(req: FullWorkflowRequest = FullWorkflowRequest()):
    """Manually trigger full workflow (background)."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    effective_limit = min(req.limit, settings.ai_run_all_max_limit)
    run_id = workflow_service.start_workflow_run("full", "manual_button", {
        "hours": req.hours,
        "since_hours": req.since_hours,
        "limit": effective_limit,
        "with_ai": req.with_ai,
        "with_ensemble": req.with_ensemble,
        "auto_lock": req.auto_lock,
        "only_missing": req.only_missing,
    })
    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")
    progress = workflow_service.get_run_progress(run_id)

    def _run_in_background():
        try:
            workflow_service.execute_full_workflow(
                run_id,
                hours=req.hours,
                since_hours=req.since_hours,
                limit=effective_limit,
                with_ai=req.with_ai,
                with_ensemble=req.with_ensemble,
                auto_lock=req.auto_lock,
                only_missing=req.only_missing,
                trigger_source="manual_button",
            )
        except Exception:
            logger.exception("full workflow background task failed")

    _workflow_executor.submit(_run_in_background)
    return _started_response(run_id, progress)


@router.get("/runs")
def workflow_runs(limit: int = 20):
    """Get recent workflow run history."""
    with session_scope() as session:
        runs = list(session.scalars(
            select(WorkflowRun)
            .order_by(WorkflowRun.started_at.desc())
            .limit(limit)
        ))
        result = []
        for r in runs:
            steps = list(session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_run_id == r.id)
                .order_by(WorkflowStep.id)
            ))
            result.append({
                "id": r.id,
                "workflow_type": r.workflow_type,
                "trigger_source": r.trigger_source,
                "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "duration_seconds": r.duration_seconds,
                "steps": [
                    {
                        "step_name": s.step_name,
                        "status": s.status,
                        "started_at": s.started_at.isoformat() if s.started_at else None,
                        "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                        "duration_seconds": s.duration_seconds,
                        "summary": s.summary_json,
                        "error_message": s.error_message,
                    }
                    for s in steps
                ],
                "summary": r.summary_json,
                "error_message": r.error_message,
                "progress": workflow_service.build_workflow_progress(steps),
            })
    return {"runs": result}


@router.get("/runs/{run_id}")
def workflow_run_detail(run_id: int):
    """Get details of a specific workflow run."""
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Workflow run not found")
        steps = list(session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .order_by(WorkflowStep.id)
        ))
        return {
            "id": run.id,
            "workflow_type": run.workflow_type,
            "trigger_source": run.trigger_source,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "duration_seconds": run.duration_seconds,
            "options": run.options_json,
            "summary": run.summary_json,
            "error_message": run.error_message,
            "steps": [
                {
                    "step_name": s.step_name,
                    "status": s.status,
                    "started_at": s.started_at.isoformat() if s.started_at else None,
                    "finished_at": s.finished_at.isoformat() if s.finished_at else None,
                    "duration_seconds": s.duration_seconds,
                    "summary": s.summary_json,
                    "error_message": s.error_message,
                }
                for s in steps
            ],
            "progress": workflow_service.build_workflow_progress(steps),
        }
