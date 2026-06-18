"""Workflow API routes - local workflow center."""

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import WorkflowRun, WorkflowStep
from app.workflows.schemas import (
    DailyOpenRequest, PreMatchRequest, PostMatchRequest,
    LockRequest, FullWorkflowRequest, UpdatePredictionsRequest,
    WorkflowRunStatus, WorkflowStepStatus,
)
from app.workflows.service import (
    get_workflow_status, run_daily_open_workflow, run_pre_match_workflow,
    run_post_match_workflow, run_lock_workflow, run_full_workflow,
    run_update_predictions_workflow,
)
from app.workflows.state import is_workflow_running
from app.workflows.scheduler import should_auto_run_daily


router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("/status")
def workflow_status():
    """Get the current workflow status for the local workflow center."""
    return get_workflow_status()


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
    """Auto-trigger daily open workflow (called when frontend first loads)."""
    if is_workflow_running():
        return {"status": "already_running", "message": "A workflow is already running"}

    # Check cooldown
    from app.workflows.state import can_auto_run
    if not can_auto_run():
        return {"status": "skipped", "message": "Already ran recently, cooldown active"}

    # Auto AI: run AI for upcoming 48h matches missing predictions
    # if with_ai is explicitly True OR auto_run_ai_on_open is enabled
    effective_with_ai = (req.with_ai or settings.auto_run_ai_on_open) and _check_ai_available_for_auto()[0]

    run_id = run_daily_open_workflow(
        hours=req.hours,
        since_hours=req.since_hours,
        limit=min(req.limit, settings.ai_run_all_max_limit),
        with_ai=effective_with_ai,
        with_ensemble=req.with_ensemble,
        auto_lock=req.auto_lock,
        only_missing=req.only_missing,
        trigger_source="auto_on_open",
    )

    if run_id < 0:
        return {"status": "already_running", "message": "A workflow is already running"}

    return {"status": "started", "run_id": run_id}


@router.post("/pre-match")
def workflow_pre_match(req: PreMatchRequest = PreMatchRequest()):
    """Manually trigger pre-match prediction workflow."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    run_id = run_pre_match_workflow(
        hours=req.hours,
        limit=min(req.limit, settings.ai_run_all_max_limit),
        with_ai=req.with_ai,
        with_ensemble=req.with_ensemble,
        only_missing=req.only_missing,
        trigger_source="manual_button",
    )

    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")

    return {"status": "started", "run_id": run_id}


@router.post("/post-match")
def workflow_post_match(req: PostMatchRequest = PostMatchRequest()):
    """Manually trigger post-match review workflow."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    run_id = run_post_match_workflow(
        since_hours=req.since_hours,
        trigger_source="manual_button",
    )

    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")

    return {"status": "started", "run_id": run_id}


@router.post("/lock")
def workflow_lock(req: LockRequest = LockRequest()):
    """Manually trigger pre-match decision snapshot workflow."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    run_id = run_lock_workflow(
        window_hours=req.window_hours,
        trigger_source="manual_button",
    )

    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")

    return {"status": "started", "run_id": run_id}


@router.post("/full")
def workflow_full(req: FullWorkflowRequest = FullWorkflowRequest()):
    """Manually trigger full workflow."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    run_id = run_full_workflow(
        hours=req.hours,
        since_hours=req.since_hours,
        limit=min(req.limit, settings.ai_run_all_max_limit),
        with_ai=req.with_ai,
        with_ensemble=req.with_ensemble,
        auto_lock=req.auto_lock,
        only_missing=req.only_missing,
        trigger_source="manual_button",
    )

    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")

    return {"status": "started", "run_id": run_id}


@router.post("/update-predictions")
def workflow_update_predictions(req: UpdatePredictionsRequest = UpdatePredictionsRequest()):
    """Manually trigger update predictions workflow."""
    if is_workflow_running():
        raise HTTPException(status_code=409, detail="A workflow is already running")

    run_id = run_update_predictions_workflow(
        limit=min(req.limit, settings.ai_run_all_max_limit),
        with_ai=req.with_ai,
        with_ensemble=req.with_ensemble,
        only_missing=req.only_missing,
        trigger_source="manual_button",
    )

    if run_id < 0:
        raise HTTPException(status_code=409, detail="A workflow is already running")

    # Return structured summary
    from datetime import datetime, timezone, timedelta
    from sqlalchemy import func
    from app.models import Match, AIPrediction
    from app.services.snapshots import count_locked_matches
    from app.ai.model_registry import list_enabled_models

    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        steps = list(session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id)
        ))

        # Build summary from step results
        ai_step = next((s for s in steps if s.step_name == "ai_prediction"), None)
        ensemble_step = next((s for s in steps if s.step_name == "ensemble_generation"), None)

        ai_summary = (ai_step.summary_json or {}) if ai_step else {}
        ensemble_summary = (ensemble_step.summary_json or {}) if ensemble_step else {}

        # Count locked skipped
        locked_skipped = count_locked_matches(session)

        # Count upcoming matches
        now = datetime.now(timezone.utc)
        matches_considered = session.scalar(
            select(func.count(Match.id))
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
        ) or 0

        overall = run.status if run else "unknown"
        status_map = {"success": "ok", "partial_success": "partial", "failed": "failed"}

        errors = [s.error_message for s in steps if s.error_message]
        # Also include run-level error if present
        if run and run.error_message:
            errors.insert(0, run.error_message)

        # Determine AI skip reason
        ai_skip_reason = None
        ai_skipped_existing = 0
        missing_ai_count = 0

        if ai_step:
            if ai_step.status == "skipped":
                reason = (ai_step.summary_json or {}).get("reason", "")
                if "with_ai=false" in reason:
                    ai_skip_reason = "AI 未启用（with_ai=false）"
                elif "not enabled" in reason:
                    ai_skip_reason = "AI 预测未启用（ENABLE_AI_PREDICTION=false）"
                else:
                    ai_skip_reason = reason or "AI 步骤被跳过"
            elif ai_step.status == "failed":
                ai_skip_reason = ai_step.error_message or "AI 预测失败"
            elif ai_step.status == "success":
                ai_skipped_existing = ai_summary.get("skipped", 0)
                # Count matches still missing AI
                enabled_versions = {m.model_version for m in list_enabled_models()}
                upcoming = list(session.scalars(
                    select(Match)
                    .where(Match.status != "final")
                    .where(Match.kickoff >= now)
                    .where(Match.kickoff <= now + timedelta(hours=48))
                ))
                for m in upcoming:
                    existing = list(session.scalars(
                        select(AIPrediction)
                        .where(AIPrediction.match_id == m.id)
                        .where(AIPrediction.error_code.is_(None))
                        .where(AIPrediction.parsed_home_win.isnot(None))
                    ))
                    covered_versions = {p.model_version for p in existing}
                    if enabled_versions - covered_versions:
                        missing_ai_count += 1
                if ai_summary.get("success", 0) == 0 and ai_summary.get("failed", 0) == 0:
                    ai_skip_reason = "没有可预测的比赛" if matches_considered == 0 else "所有比赛已有 AI 预测"

        return {
            "status": status_map.get(overall, overall),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "matches_considered": matches_considered,
            "predictions_updated": 1 if any(s.step_name == "pre_match_recompute" and s.status == "success" for s in steps) else 0,
            "ai_success": ai_summary.get("success", 0),
            "ai_failed": ai_summary.get("failed", 0),
            "ai_skipped_existing": ai_skipped_existing,
            "ai_skip_reason": ai_skip_reason,
            "missing_ai_count": missing_ai_count,
            "ensemble_updated": ensemble_summary.get("success", 0),
            "locked_skipped": locked_skipped,
            "errors": errors,
            "run_id": run_id,
        }


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
                        "duration_seconds": s.duration_seconds,
                        "error_message": s.error_message,
                    }
                    for s in steps
                ],
                "error_message": r.error_message,
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
        }
