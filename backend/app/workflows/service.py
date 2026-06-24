"""Workflow service - shared by API and CLI scripts."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select, func

from app.config import settings, PROJECT_ROOT
from app.db import session_scope
from app.logging_config import set_workflow_context, clear_workflow_context
from app.models import Match, PredictionSnapshot, AIPrediction, EnsemblePrediction, WorkflowRun, WorkflowStep
from app.ai.lock_status import compute_match_lock_status
from app.workflows.state import set_current_run, is_workflow_running, try_start_workflow

logger = logging.getLogger(__name__)

# China timezone — "today" should be determined by the user's local calendar.
# See state.py for rationale.
_CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware (UTC). SQLite returns naive datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


STEP_NAMES = [
    "refresh_results",
    "post_match_recompute",
    "post_match_score",
    "pre_match_recompute",
    "ai_prediction",
    "ensemble_generation",
    "lock_predictions",
    "accuracy_command_update",
    "artifact_generation",
]

TERMINAL_STEP_STATUSES = {"success", "failed", "skipped", "partial_success"}


def build_workflow_progress(steps: list[WorkflowStep]) -> dict[str, Any]:
    """Build a compact progress payload for API consumers."""
    total = len(steps)
    completed = sum(1 for step in steps if step.status in TERMINAL_STEP_STATUSES)
    running_step = next((step.step_name for step in steps if step.status == "running"), None)
    failed_steps = [
        {"step_name": step.step_name, "error_message": step.error_message}
        for step in steps
        if step.status == "failed"
    ]
    percent = int(round((completed / total) * 100)) if total else 0
    return {
        "total_steps": total,
        "completed_steps": completed,
        "percent": percent,
        "running_step": running_step,
        "failed_steps": failed_steps,
    }


def get_run_progress(run_id: int) -> dict[str, Any]:
    """Return progress for a workflow run."""
    with session_scope() as session:
        steps = list(session.scalars(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .order_by(WorkflowStep.id)
        ))
        return build_workflow_progress(steps)


def _create_run(workflow_type: str, trigger_source: str, options: dict | None = None) -> int:
    """Create a new WorkflowRun with all steps."""
    with session_scope() as session:
        run = WorkflowRun(
            workflow_type=workflow_type,
            trigger_source=trigger_source,
            status="running",
            options_json=options,
        )
        session.add(run)
        session.flush()
        run_id = run.id
        for step_name in STEP_NAMES:
            step = WorkflowStep(workflow_run_id=run_id, step_name=step_name, status="pending")
            session.add(step)
        session.commit()
    set_workflow_context(run_id)
    return run_id


def _mark_run_not_started(run_id: int, error: str) -> None:
    """Mark a run that was created but could not reserve the workflow lock."""
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        if run:
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error_message = error
            session.commit()


def start_workflow_run(workflow_type: str, trigger_source: str, options: dict | None = None) -> int:
    """Create a workflow run and reserve the in-process workflow lock."""
    run_id = _create_run(workflow_type, trigger_source, options)
    if not try_start_workflow(run_id):
        _mark_run_not_started(run_id, "A workflow is already running")
        clear_workflow_context()
        return -1
    return run_id


def _update_step(run_id: int, step_name: str, status: str, summary: dict | None = None, error: str | None = None):
    """Update a workflow step's status."""
    with session_scope() as session:
        step = session.scalar(
            select(WorkflowStep)
            .where(WorkflowStep.workflow_run_id == run_id)
            .where(WorkflowStep.step_name == step_name)
        )
        if step:
            step.status = status
            if status == "running":
                step.started_at = datetime.now(timezone.utc)
            elif status in ("success", "failed", "skipped"):
                step.finished_at = datetime.now(timezone.utc)
                started = _ensure_utc(step.started_at)
                if started:
                    step.duration_seconds = (step.finished_at - started).total_seconds()
            if summary:
                step.summary_json = summary
            if error:
                step.error_message = error
            session.commit()


def _finish_run(run_id: int, status: str, summary: dict | None = None, error: str | None = None):
    """Finish a workflow run."""
    set_current_run(None)
    clear_workflow_context()
    with session_scope() as session:
        run = session.get(WorkflowRun, run_id)
        if run:
            run.status = status
            finished = datetime.now(timezone.utc)
            run.finished_at = finished
            started = _ensure_utc(run.started_at)
            if started:
                run.duration_seconds = (finished - started).total_seconds()
            if summary:
                run.summary_json = summary
            if error:
                run.error_message = error
            session.commit()


def _get_upcoming_matches_info() -> dict:
    """Get info about upcoming matches."""
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        count_24h = session.scalar(
            select(func.count(Match.id))
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
            .where(Match.kickoff <= now + timedelta(hours=24))
        ) or 0

        count_48h = session.scalar(
            select(func.count(Match.id))
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
            .where(Match.kickoff <= now + timedelta(hours=48))
        ) or 0

        # Check baseline/AI/ensemble readiness
        upcoming = list(session.scalars(
            select(Match)
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
            .where(Match.kickoff <= now + timedelta(hours=48))
        ))

        baseline_ready = 0
        ai_ready = 0
        ensemble_ready = 0
        needs_ai = 0

        for m in upcoming:
            snap = session.scalar(
                select(PredictionSnapshot)
                .where(PredictionSnapshot.match_id == m.id)
                .order_by(PredictionSnapshot.snapshotted_at.desc())
                .limit(1)
            )
            if snap:
                baseline_ready += 1

            ai_preds = list(session.scalars(
                select(AIPrediction)
                .where(AIPrediction.match_id == m.id)
                .where(AIPrediction.error_code.is_(None))
            ))
            ai_versions = {p.model_version for p in ai_preds}
            if ai_versions:
                ai_ready += 1
            else:
                needs_ai += 1

            ens = session.scalar(
                select(EnsemblePrediction)
                .where(EnsemblePrediction.match_id == m.id)
                .limit(1)
            )
            if ens:
                ensemble_ready += 1

        return {
            "count_24h": count_24h,
            "count_48h": count_48h,
            "baseline_ready": baseline_ready,
            "ai_ready": ai_ready,
            "ensemble_ready": ensemble_ready,
            "needs_ai": needs_ai,
        }


def _get_yesterday_matches_info() -> dict:
    """Get info about yesterday's matches."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)
    with session_scope() as session:
        count = session.scalar(
            select(func.count(Match.id))
            .where(Match.status == "final")
            .where(Match.kickoff >= yesterday)
        ) or 0

        scored = session.scalar(
            select(func.count(PredictionSnapshot.match_id))
            .where(PredictionSnapshot.is_pre_match_locked == True)
            .where(PredictionSnapshot.kickoff >= yesterday)
        ) or 0

        return {"count": count, "scored": min(scored, count), "needs_review": count > scored}


def _get_lock_status_info() -> dict:
    """Get info about 24h lock status.

    Note: 24h locking is for backward compatibility only, not core scoring.
    The scoring system uses the latest pre-match snapshot before kickoff.
    """
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=24)
    with session_scope() as session:
        near_kickoff = list(session.scalars(
            select(Match)
            .where(Match.status != "final")
            .where(Match.kickoff >= now)
            .where(Match.kickoff <= window_end)
        ))

        locked = 0
        needs_lock = 0
        real_time_only = 0

        for m in near_kickoff:
            lock = compute_match_lock_status(m, now)
            snap = session.scalar(
                select(PredictionSnapshot)
                .where(PredictionSnapshot.match_id == m.id)
                .where(PredictionSnapshot.is_pre_match_locked == True)
                .limit(1)
            )
            if snap:
                locked += 1
            else:
                needs_lock += 1
            if lock.real_time_only:
                real_time_only += 1

        return {
            "matches_near_kickoff": len(near_kickoff),
            "locked": locked,
            "needs_lock": needs_lock,
            "real_time_only": real_time_only,
        }


def _get_decision_snapshot_status_info() -> dict:
    """Get decision snapshot status for upcoming and recent matches.

    Under the new scoring rule, any pre-kickoff snapshot is a valid
    decision snapshot. The latest one before kickoff is used for scoring.
    """
    from app.ai.lock_status import compute_decision_snapshot_status

    now = datetime.now(timezone.utc)
    cutoff_future = now + timedelta(days=7)
    cutoff_past = now - timedelta(days=3)

    with session_scope() as session:
        matches = list(session.scalars(
            select(Match)
            .where(Match.kickoff >= cutoff_past)
            .where(Match.kickoff <= cutoff_future)
            .order_by(Match.kickoff)
        ))

        matches_total = len(matches)
        snapshots_ready = 0
        missing = 0
        last_snapshot_at = None

        for match in matches:
            snapshots = list(session.scalars(
                select(PredictionSnapshot)
                .where(PredictionSnapshot.match_id == match.id)
                .order_by(PredictionSnapshot.snapshotted_at.desc())
            ))

            status = compute_decision_snapshot_status(match, snapshots, now)

            if status.has_decision_snapshot:
                snapshots_ready += 1
                if last_snapshot_at is None or (status.snapshot_at and status.snapshot_at > last_snapshot_at):
                    last_snapshot_at = status.snapshot_at
            else:
                missing += 1

        overall_status = "ready" if missing == 0 and matches_total > 0 else ("partial" if snapshots_ready > 0 else "none")

        return {
            "status": overall_status,
            "matches_total": matches_total,
            "snapshots_ready": snapshots_ready,
            "missing": missing,
            "last_snapshot_at": last_snapshot_at.isoformat() if last_snapshot_at else None,
            "rule": "latest_pre_match_snapshot_before_kickoff",
        }


def _get_today_ai_stats() -> dict:
    """Get today's AI call statistics with detailed validity breakdown.

    "Today" is determined by China Standard Time (UTC+8), not UTC, so that AI
    calls made in the early morning hours (CST) don't vanish at 8:00 AM CST.
    """
    now_utc = datetime.now(timezone.utc)
    now_china = now_utc.astimezone(_CHINA_TZ)
    today_start_china = now_china.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start = today_start_china.astimezone(timezone.utc)

    with session_scope() as session:
        from app.models import AIPrediction
        from app.ai.model_registry import list_enabled_models

        # Configured models count
        enabled_models = list_enabled_models()
        configured_models = len(enabled_models)

        # Today's predictions
        today_preds = list(session.scalars(
            select(AIPrediction)
            .where(AIPrediction.created_at >= today_start)
        ))

        success = 0
        failed = 0
        parse_error = 0
        skipped = 0
        cooldown_skipped = False
        only_missing_skipped = 0

        for p in today_preds:
            if p.error_code is None and p.parsed_home_win is not None:
                success += 1
            elif p.error_code == "parse_failed" or p.error_code == "invalid_probabilities":
                parse_error += 1
                failed += 1
            else:
                failed += 1

        # Load matches for kickoff comparison
        match_ids = {p.match_id for p in today_preds}
        match_kickoffs: dict[int, datetime | None] = {}
        if match_ids:
            matches = list(session.scalars(
                select(Match).where(Match.id.in_(match_ids))
            ))
            match_kickoffs = {m.id: _ensure_utc(m.kickoff) for m in matches}

        # Count effective for ensemble (success + not real_time_only)
        effective_for_ensemble = sum(
            1 for p in today_preds
            if p.error_code is None
            and p.parsed_home_win is not None
            and not p.real_time_only
        )

        # Count locked for scoring (success + pre-match or fallback locked)
        locked_for_scoring_count = sum(
            1 for p in today_preds
            if p.error_code is None
            and p.parsed_home_win is not None
            and (p.is_pre_match_locked or p.is_fallback_locked)
        )

        # Count eligible for scoring (success + created_at < kickoff)
        # This matches the scoring system's actual criterion in evaluation.py
        eligible_for_scoring_count = sum(
            1 for p in today_preds
            if p.error_code is None
            and p.parsed_home_win is not None
            and match_kickoffs.get(p.match_id) is not None
            and (_ensure_utc(p.created_at) < match_kickoffs[p.match_id])
        )

        # Backward-compatible alias
        effective_for_scoring_count = eligible_for_scoring_count

        # Check if any workflow was skipped due to cooldown today
        from app.models import WorkflowStep
        ai_steps = list(session.scalars(
            select(WorkflowStep)
            .join(WorkflowRun, WorkflowStep.workflow_run_id == WorkflowRun.id)
            .where(WorkflowStep.step_name == "ai_prediction")
            .where(WorkflowStep.status == "skipped")
            .where(WorkflowRun.started_at >= today_start)
        ))
        for step in ai_steps:
            summary = step.summary_json or {}
            reason = summary.get("reason", "")
            if "cooldown" in reason.lower() or "already" in reason.lower():
                cooldown_skipped = True
            if "only_missing" in reason.lower() or "with_ai=false" in reason.lower():
                only_missing_skipped += 1

        # Count total skipped from AI step summaries
        for step in list(session.scalars(
            select(WorkflowStep)
            .join(WorkflowRun, WorkflowStep.workflow_run_id == WorkflowRun.id)
            .where(WorkflowStep.step_name == "ai_prediction")
            .where(WorkflowStep.status == "success")
            .where(WorkflowRun.started_at >= today_start)
        )):
            summary = step.summary_json or {}
            skipped += summary.get("skipped", 0)

        return {
            "configured_models": configured_models,
            "attempted": success + failed,
            "today_ai_calls": success,
            "today_ai_failed": failed,
            "parse_error": parse_error,
            "today_ai_skipped": skipped,
            "effective_for_ensemble": effective_for_ensemble,
            "locked_for_scoring_count": locked_for_scoring_count,
            "eligible_for_scoring_count": eligible_for_scoring_count,
            "effective_for_scoring": effective_for_scoring_count,
            "cooldown_skipped": cooldown_skipped,
            "only_missing_skipped": only_missing_skipped,
        }


def _check_ai_available() -> tuple[bool, str]:
    """Check if AI prediction is available. Returns (available, reason)."""
    from app.config import settings
    if not settings.enable_ai_prediction:
        return False, "AI 预测未启用（ENABLE_AI_PREDICTION=false）"

    # Check if at least one provider has an API key configured
    # Auto-discover from settings using ai_models.yaml provider list
    from app.ai.model_registry import list_enabled_providers
    has_key = False
    missing_providers = []
    for provider_config in list_enabled_providers():
        attr = provider_config.api_key_env.lower()
        key = getattr(settings, attr, "")
        if key:
            has_key = True
        else:
            missing_providers.append(provider_config.provider_name)

    if not has_key:
        return False, f"未配置 API Key（{', '.join(missing_providers)}）"

    return True, "AI 可用"


def _compute_button_states(
    running: bool,
    cooldown_active: bool,
    upcoming_info: dict,
    yesterday_info: dict,
    lock_info: dict,
) -> dict:
    """Compute button enabled/disabled states for the frontend."""
    from app.ai.model_registry import list_enabled_models

    needs_ai = upcoming_info.get("needs_ai", 0)

    # daily_open: disabled if workflow running OR cooldown active
    if running:
        daily_open = {"enabled": False, "reason": "当前已有工作流正在运行"}
    elif cooldown_active:
        daily_open = {"enabled": False, "reason": "60分钟冷却期内"}
    else:
        daily_open = {"enabled": True, "reason": "可运行"}

    # pre_match: disabled only if workflow running (manual buttons bypass cooldown)
    if running:
        pre_match = {"enabled": False, "reason": "当前已有工作流正在运行"}
    else:
        pre_match = {"enabled": True, "reason": "可运行"}

    # ai_prediction: disabled if workflow running OR AI not enabled OR no API key OR needs_ai == 0
    ai_available, ai_reason = _check_ai_available()
    num_enabled_models = len(list_enabled_models())
    estimated_calls = min(needs_ai, settings.ai_run_all_max_limit) * num_enabled_models
    if running:
        ai_prediction = {"enabled": False, "reason": "当前已有工作流正在运行", "estimated_calls": 0, "needs_ai": needs_ai}
    elif not ai_available:
        ai_prediction = {"enabled": False, "reason": ai_reason, "estimated_calls": 0, "needs_ai": needs_ai}
    elif needs_ai == 0:
        ai_prediction = {"enabled": False, "reason": "没有需要AI预测的比赛", "estimated_calls": 0, "needs_ai": 0}
    else:
        ai_prediction = {
            "enabled": True,
            "reason": f"可运行，预计处理{needs_ai}场",
            "estimated_calls": estimated_calls,
            "needs_ai": needs_ai,
        }

    # post_match: disabled if workflow running OR no finished matches to review
    has_finished = yesterday_info.get("count", 0) > 0
    needs_review = yesterday_info.get("needs_review", False)
    if running:
        post_match = {"enabled": False, "reason": "当前已有工作流正在运行"}
    elif not has_finished or not needs_review:
        post_match = {"enabled": False, "reason": "没有已完赛比赛需要复盘"}
    else:
        post_match = {"enabled": True, "reason": "可运行"}

    # lock: disabled if workflow running OR no matches near kickoff
    needs_lock = lock_info.get("needs_lock", 0)
    if running:
        lock = {"enabled": False, "reason": "当前已有工作流正在运行"}
    elif needs_lock == 0:
        lock = {"enabled": False, "reason": "没有即将开赛的比赛需要锁定"}
    else:
        lock = {"enabled": True, "reason": "可运行"}

    # full: disabled only if workflow running
    if running:
        full = {"enabled": False, "reason": "当前已有工作流正在运行", "estimated_calls": 0}
    else:
        full = {"enabled": True, "reason": "可运行，但会调用AI API产生费用", "estimated_calls": estimated_calls}

    return {
        "daily_open": daily_open,
        "pre_match": pre_match,
        "ai_prediction": ai_prediction,
        "post_match": post_match,
        "lock": lock,
        "full": full,
    }


def _get_ai_status_info() -> dict:
    """Get structured AI status for the workflow status response."""
    from app.ai.model_registry import list_enabled_models, list_enabled_providers
    from app.config import settings as _settings

    configured_models = len(list_enabled_models())

    # Check API key availability
    has_key = False
    for provider_config in list_enabled_providers():
        attr = provider_config.api_key_env.lower()
        key = getattr(_settings, attr, "")
        if key:
            has_key = True
            break

    today_stats = _get_today_ai_stats()

    return {
        "configured_models": configured_models,
        "attempted": today_stats.get("attempted", 0),
        "success": today_stats.get("today_ai_calls", 0),
        "failed": today_stats.get("today_ai_failed", 0),
        "parse_error": today_stats.get("parse_error", 0),
        "effective_for_ensemble": today_stats.get("effective_for_ensemble", 0),
        "locked_for_scoring_count": today_stats.get("locked_for_scoring_count", 0),
        "eligible_for_scoring_count": today_stats.get("eligible_for_scoring_count", 0),
        "effective_for_scoring": today_stats.get("effective_for_scoring", 0),
        "api_key_ready": has_key,
    }


def get_workflow_status() -> dict:
    """Get the current workflow status for the frontend."""
    from app.workflows.state import get_today_status, can_auto_run
    from app.workflows.schemas import WorkflowRunStatus, WorkflowStepStatus

    today_status_result = get_today_status()
    today_status = today_status_result["status"]
    today_failed_steps = today_status_result.get("failed_steps", [])

    recommended_action = "none"
    if today_status == "needs_run" and can_auto_run():
        recommended_action = "run_daily_open_workflow"
    elif today_status == "running":
        recommended_action = "wait"

    # Get last run
    last_run_data = None
    with session_scope() as session:
        last_run = session.scalar(
            select(WorkflowRun)
            .order_by(WorkflowRun.started_at.desc())
            .limit(1)
        )
        if last_run:
            steps = list(session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_run_id == last_run.id)
                .order_by(WorkflowStep.id)
            ))
            last_run_data = {
                "id": last_run.id,
                "workflow_type": last_run.workflow_type,
                "trigger_source": last_run.trigger_source,
                "status": last_run.status,
                "started_at": last_run.started_at,
                "finished_at": last_run.finished_at,
                "duration_seconds": last_run.duration_seconds,
                "steps": [
                    {
                        "step_name": s.step_name,
                        "status": s.status,
                        "started_at": s.started_at,
                        "finished_at": s.finished_at,
                        "duration_seconds": s.duration_seconds,
                        "summary": s.summary_json,
                        "error_message": s.error_message,
                    }
                    for s in steps
                ],
                "summary": last_run.summary_json,
                "error_message": last_run.error_message,
                "progress": build_workflow_progress(steps),
            }

    running = is_workflow_running()
    cooldown_active = not can_auto_run()
    upcoming_info = _get_upcoming_matches_info()
    yesterday_info = _get_yesterday_matches_info()
    lock_info = _get_lock_status_info()

    button_states = _compute_button_states(
        running=running,
        cooldown_active=cooldown_active,
        upcoming_info=upcoming_info,
        yesterday_info=yesterday_info,
        lock_info=lock_info,
    )

    decision_snapshot_info = _get_decision_snapshot_status_info()
    ai_status_info = _get_ai_status_info()

    # Compute next_action
    next_action = {"message": "", "action": "none"}
    if running:
        next_action = {"message": "工作流正在运行中，请等待完成", "action": "wait"}
    elif today_status == "needs_run":
        next_action = {"message": "建议运行每日更新，同步赛果和今日赛程", "action": "run_daily_open_workflow"}
    elif today_status == "failed":
        next_action = {"message": "今日流程失败，请查看日志并重试", "action": "run_daily_open_workflow"}
    elif today_status == "partial_success":
        next_action = {"message": "今日流程部分失败，请查看日志", "action": "view_logs"}
    elif upcoming_info.get("needs_ai", 0) > 0:
        next_action = {"message": f"有 {upcoming_info['needs_ai']} 场比赛需要 AI 预测", "action": "run_ai_prediction"}
    elif today_status in ("already_run", "completed"):
        next_action = {"message": "今日预测已准备完成", "action": "none"}

    return {
        "today_status": today_status,
        "today_failed_steps": today_failed_steps,
        "last_run_at": last_run_data["started_at"] if last_run_data else None,
        "recommended_action": recommended_action,
        "yesterday_matches": yesterday_info,
        "upcoming_matches": upcoming_info,
        "lock_status": lock_info,
        "decision_snapshot_status": decision_snapshot_info,
        "last_run": last_run_data,
        "ai_stats": _get_today_ai_stats(),
        "ai_status": ai_status_info,
        "next_action": next_action,
        "button_states": button_states,
    }


def _run_refresh_step(run_id: int):
    """Step: refresh data from providers."""
    _update_step(run_id, "refresh_results", "running")
    try:
        from app.services.refresh import refresh_tournament
        from app.api.routes.dashboard_routes import _build_providers
        with session_scope() as session:
            refresh_tournament(
                session,
                providers=_build_providers(),
                iterations=settings.simulation_iterations,
                seed=settings.simulation_seed,
                recompute_predictions=False,
            )
        _update_step(run_id, "refresh_results", "success", {"refreshed": True})
    except Exception as e:
        logger.error(f"Refresh step failed: {e}")
        _update_step(run_id, "refresh_results", "failed", error=str(e))


def _run_recompute_step(run_id: int, step_name: str = "pre_match_recompute"):
    """Step: recompute baseline predictions."""
    _update_step(run_id, step_name, "running")
    try:
        from app.services.recompute import recompute_all
        with session_scope() as session:
            recompute_all(session, iterations=settings.simulation_iterations, seed=settings.simulation_seed)
        _update_step(run_id, step_name, "success", {"recomputed": True})
    except Exception as e:
        logger.error(f"Recompute step failed: {e}")
        _update_step(run_id, step_name, "failed", error=str(e))


def _run_score_step(run_id: int):
    """Step: score final matches and persist one result per active revision."""
    _update_step(run_id, "post_match_score", "running")
    try:
        from app.models import DashboardRevision, ModelScore
        from app.services.scoring import save_model_score, score_model

        with session_scope() as session:
            revision = session.scalar(
                select(DashboardRevision)
                .where(DashboardRevision.active.is_(True))
                .order_by(DashboardRevision.id.desc())
                .limit(1)
            )
            if revision is None:
                _update_step(run_id, "post_match_score", "skipped", {"reason": "no_active_revision"})
                return

            report = score_model(session)
            existing = session.scalar(
                select(ModelScore.id).where(ModelScore.revision_id == revision.id).limit(1)
            )
            saved = False
            if report.matches_scored > 0 and existing is None:
                save_model_score(session, report, revision.id)
                saved = True

            # Update adaptive weights after scoring
            adaptive_result = None
            if saved:
                try:
                    from app.services.adaptive_weights import compute_adaptive_weights
                    adaptive_result = compute_adaptive_weights(session)
                except Exception as aw_err:
                    logger.warning(f"Adaptive weights update failed (non-critical): {aw_err}")

        summary = {
            "revision_id": revision.id,
            "matches_scored": report.matches_scored,
            "saved": saved,
            "reason": "already_saved" if existing is not None else None,
        }
        if adaptive_result and adaptive_result.get("is_adaptive"):
            summary["adaptive_weights"] = {
                "system": adaptive_result["weights"].get("system"),
                "market": adaptive_result["weights"].get("market"),
                "is_adaptive": True,
            }

        _update_step(run_id, "post_match_score", "success", summary)
    except Exception as e:
        logger.error(f"Score step failed: {e}")
        _update_step(run_id, "post_match_score", "failed", error=str(e))


def _run_ai_prediction_step(run_id: int, limit: int = 10, only_missing: bool = True, retry_failed: bool = False):
    """Step: run AI predictions (sync version for CLI/scripts, runs in background thread)."""
    _update_step(run_id, "ai_prediction", "running")
    try:
        from app.ai.service import run_ai_predictions_batch, is_ai_enabled
        if not is_ai_enabled():
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "AI not enabled"})
            return

        clamped_limit = min(limit, settings.ai_run_all_max_limit)
        with session_scope() as session:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None

            if loop and loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    results = pool.submit(
                        asyncio.run,
                        run_ai_predictions_batch(
                            session, limit=clamped_limit, only_missing=only_missing, retry_failed=retry_failed
                        )
                    ).result()
            else:
                results = asyncio.run(run_ai_predictions_batch(
                    session, limit=clamped_limit, only_missing=only_missing, retry_failed=retry_failed
                ))

        success = sum(1 for r in results if r.get("status") != "error" and r.get("status") != "skipped")
        failed = sum(1 for r in results if r.get("status") == "error")
        skipped = sum(1 for r in results if r.get("status") == "skipped")

        _update_step(run_id, "ai_prediction", "success" if failed == 0 else "failed" if success == 0 else "success",
                     {"success": success, "failed": failed, "skipped": skipped, "api_calls": success})
    except Exception as e:
        logger.error(f"AI prediction step failed: {e}")
        _update_step(run_id, "ai_prediction", "failed", error=str(e))


async def _run_ai_prediction_step_async(run_id: int, limit: int = 10, only_missing: bool = True, retry_failed: bool = False):
    """Step: run AI predictions (async version for use within async context)."""
    _update_step(run_id, "ai_prediction", "running")
    try:
        from app.ai.service import run_ai_predictions_batch, is_ai_enabled
        if not is_ai_enabled():
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "AI not enabled"})
            return

        clamped_limit = min(limit, settings.ai_run_all_max_limit)
        with session_scope() as session:
            results = await run_ai_predictions_batch(
                session, limit=clamped_limit, only_missing=only_missing, retry_failed=retry_failed
            )

        success = sum(1 for r in results if r.get("status") != "error" and r.get("status") != "skipped")
        failed = sum(1 for r in results if r.get("status") == "error")
        skipped = sum(1 for r in results if r.get("status") == "skipped")

        _update_step(run_id, "ai_prediction", "success" if failed == 0 else "failed" if success == 0 else "success",
                     {"success": success, "failed": failed, "skipped": skipped, "api_calls": success})
    except Exception as e:
        logger.error(f"AI prediction step failed: {e}")
        _update_step(run_id, "ai_prediction", "failed", error=str(e))


def _run_ensemble_step(run_id: int):
    """Step: generate ensemble predictions."""
    _update_step(run_id, "ensemble_generation", "running")
    try:
        from app.ai.ensemble import compute_ensemble
        now = datetime.now(timezone.utc)
        with session_scope() as session:
            matches = list(session.scalars(
                select(Match)
                .where(Match.status != "final")
                .where(Match.kickoff >= now)
                .order_by(Match.kickoff)
            ))

        success = 0
        failed = 0
        for m in matches:
            try:
                with session_scope() as session:
                    result = compute_ensemble(session, m.id)
                if result.get("status") == "success":
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                logger.error(f"Ensemble failed for {m.id}: {e}")

        _update_step(run_id, "ensemble_generation", "success" if failed == 0 else "partial_success",
                     {"success": success, "failed": failed})
    except Exception as e:
        logger.error(f"Ensemble step failed: {e}")
        _update_step(run_id, "ensemble_generation", "failed", error=str(e))


def _run_lock_step(run_id: int, window_hours: int = 24):
    """Step: lock predictions for matches within 24h of kickoff."""
    _update_step(run_id, "lock_predictions", "running")
    try:
        from app.services.snapshots import lock_due_predictions
        with session_scope() as session:
            counts = lock_due_predictions(session, window_hours=window_hours)

        _update_step(
            run_id,
            "lock_predictions",
            "success",
            {**counts, "locked_count": counts["baseline"] + counts["ai"] + counts["ensemble"]},
        )
    except Exception as e:
        logger.error(f"Lock step failed: {e}")
        _update_step(run_id, "lock_predictions", "failed", error=str(e))


def _run_accuracy_update_step(run_id: int):
    """Step: update Accuracy Command Center."""
    _update_step(run_id, "accuracy_command_update", "running")
    try:
        from app.services.accuracy_command import get_accuracy_command_center
        with session_scope() as session:
            report = get_accuracy_command_center(session)
        _update_step(
            run_id,
            "accuracy_command_update",
            "success",
            {
                "updated": True,
                "sample_count": report.get("sample_count", 0),
                "recommended_model": report.get("recommended_model"),
            },
        )
    except Exception as e:
        logger.error(f"Accuracy update step failed: {e}")
        _update_step(run_id, "accuracy_command_update", "failed", error=str(e))


def _run_artifact_step(run_id: int):
    """Step: generate artifact reports."""
    _update_step(run_id, "artifact_generation", "running")
    try:
        artifacts_dir = PROJECT_ROOT / "artifacts"
        artifacts_dir.mkdir(exist_ok=True)
        summary = {
            "workflow_type": "?",
            "trigger_source": "?",
            "started_at": "?",
            "finished_at": "?",
            "steps": [],
        }

        with session_scope() as session:
            run = session.get(WorkflowRun, run_id)
            if run:
                summary = run.summary_json or {}
                summary["workflow_type"] = run.workflow_type
                summary["trigger_source"] = run.trigger_source
                summary["started_at"] = run.started_at.isoformat() if run.started_at else None
                summary["finished_at"] = run.finished_at.isoformat() if run.finished_at else None

                steps = list(session.scalars(
                    select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id)
                ))
                summary["steps"] = [
                    {"name": s.step_name, "status": s.status, "duration": s.duration_seconds, "error": s.error_message}
                    for s in steps
                ]

        path = artifacts_dir / "local_workflow_report.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write("# Local Workflow Report\n\n")
            f.write(f"**Type:** {summary.get('workflow_type', '?')}\n")
            f.write(f"**Trigger:** {summary.get('trigger_source', '?')}\n")
            f.write(f"**Started:** {summary.get('started_at', '?')}\n")
            f.write(f"**Finished:** {summary.get('finished_at', '?')}\n\n")
            f.write("## Steps\n\n")
            for step in summary.get("steps", []):
                f.write(f"- {step['name']}: {step['status']}" +
                        (f" ({step['duration']:.1f}s)" if step.get('duration') else "") +
                        (f" - ERROR: {step['error']}" if step.get('error') else "") + "\n")

        _update_step(run_id, "artifact_generation", "success", {"artifact_path": str(path)})
    except Exception as e:
        logger.error(f"Artifact step failed: {e}")
        _update_step(run_id, "artifact_generation", "failed", error=str(e))


def _determine_overall_status(run_id: int) -> str:
    """Determine overall workflow status from steps."""
    with session_scope() as session:
        steps = list(session.scalars(
            select(WorkflowStep).where(WorkflowStep.workflow_run_id == run_id)
        ))

    has_failed = any(s.status == "failed" for s in steps)
    has_success = any(s.status == "success" for s in steps)

    if has_failed and has_success:
        return "partial_success"
    elif has_failed:
        return "failed"
    else:
        return "success"


def execute_daily_open_workflow(
    run_id: int,
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = False,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "auto_on_open",
) -> int:
    """Execute an already-created daily open workflow run."""
    set_workflow_context(run_id)
    try:
        _run_refresh_step(run_id)
        _run_recompute_step(run_id, "post_match_recompute")
        _run_score_step(run_id)
        _update_step(run_id, "pre_match_recompute", "skipped", {"reason": "covered_by_post_match_recompute"})

        if with_ai:
            _run_ai_prediction_step(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        if auto_lock:
            _run_lock_step(run_id, settings.workflow_default_lock_window_hours)
        else:
            _update_step(run_id, "lock_predictions", "skipped", {"reason": "auto_lock=false"})

        _run_accuracy_update_step(run_id)
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"daily_open workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


def run_daily_open_workflow(
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = False,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "auto_on_open",
) -> int:
    """Run the daily open workflow."""
    run_id = start_workflow_run("daily_open", trigger_source, {
        "hours": hours, "since_hours": since_hours, "limit": limit,
        "with_ai": with_ai, "with_ensemble": with_ensemble,
        "auto_lock": auto_lock, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    return execute_daily_open_workflow(
        run_id,
        hours=hours,
        since_hours=since_hours,
        limit=limit,
        with_ai=with_ai,
        with_ensemble=with_ensemble,
        auto_lock=auto_lock,
        only_missing=only_missing,
        trigger_source=trigger_source,
    )


def execute_pre_match_workflow(
    run_id: int,
    hours: int = 48,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Execute an already-created pre-match workflow run."""
    set_workflow_context(run_id)
    try:
        _update_step(run_id, "refresh_results", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "post_match_recompute", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "post_match_score", "skipped", {"reason": "not_in_pre_match"})
        _run_recompute_step(run_id, "pre_match_recompute")

        if with_ai:
            _run_ai_prediction_step(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        _update_step(run_id, "lock_predictions", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "accuracy_command_update", "skipped", {"reason": "not_in_pre_match"})
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"pre_match workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


def run_pre_match_workflow(
    hours: int = 48,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Run the pre-match workflow."""
    run_id = start_workflow_run("pre_match", trigger_source, {
        "hours": hours, "limit": limit, "with_ai": with_ai,
        "with_ensemble": with_ensemble, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    return execute_pre_match_workflow(
        run_id,
        hours=hours,
        limit=limit,
        with_ai=with_ai,
        with_ensemble=with_ensemble,
        only_missing=only_missing,
        trigger_source=trigger_source,
    )


def execute_post_match_workflow(
    run_id: int,
    since_hours: int = 24,
    trigger_source: str = "manual_button",
) -> int:
    """Execute an already-created post-match workflow run."""
    set_workflow_context(run_id)
    try:
        _run_refresh_step(run_id)
        _run_recompute_step(run_id, "post_match_recompute")
        _run_score_step(run_id)
        _update_step(run_id, "pre_match_recompute", "skipped", {"reason": "not_in_post_match"})
        _update_step(run_id, "ai_prediction", "skipped", {"reason": "not_in_post_match"})
        _update_step(run_id, "ensemble_generation", "skipped", {"reason": "not_in_post_match"})
        _update_step(run_id, "lock_predictions", "skipped", {"reason": "not_in_post_match"})
        _run_accuracy_update_step(run_id)
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"post_match workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


def run_post_match_workflow(
    since_hours: int = 24,
    trigger_source: str = "manual_button",
) -> int:
    """Run the post-match workflow."""
    run_id = start_workflow_run("post_match", trigger_source, {"since_hours": since_hours})
    if run_id < 0:
        return -1
    return execute_post_match_workflow(
        run_id,
        since_hours=since_hours,
        trigger_source=trigger_source,
    )


def execute_lock_workflow(
    run_id: int,
    window_hours: int = 24,
    trigger_source: str = "manual_button",
) -> int:
    """Execute an already-created lock workflow run."""
    set_workflow_context(run_id)
    try:
        for step in ["refresh_results", "post_match_recompute", "post_match_score",
                      "pre_match_recompute", "ai_prediction", "ensemble_generation"]:
            _update_step(run_id, step, "skipped", {"reason": "not_in_lock"})

        _run_lock_step(run_id, window_hours)
        _update_step(run_id, "accuracy_command_update", "skipped", {"reason": "not_in_lock"})
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"lock workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


def run_lock_workflow(
    window_hours: int = 24,
    trigger_source: str = "manual_button",
) -> int:
    """Run the lock workflow."""
    run_id = start_workflow_run("lock", trigger_source, {"window_hours": window_hours})
    if run_id < 0:
        return -1
    return execute_lock_workflow(
        run_id,
        window_hours=window_hours,
        trigger_source=trigger_source,
    )


def execute_full_workflow(
    run_id: int,
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Execute an already-created full workflow run."""
    set_workflow_context(run_id)
    try:
        _run_refresh_step(run_id)
        _run_recompute_step(run_id, "post_match_recompute")
        _run_score_step(run_id)
        _update_step(run_id, "pre_match_recompute", "skipped", {"reason": "covered_by_post_match_recompute"})

        if with_ai:
            _run_ai_prediction_step(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        if auto_lock:
            _run_lock_step(run_id, settings.workflow_default_lock_window_hours)
        else:
            _update_step(run_id, "lock_predictions", "skipped", {"reason": "auto_lock=false"})

        _run_accuracy_update_step(run_id)
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"full workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


def run_full_workflow(
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Run the full workflow."""
    run_id = start_workflow_run("full", trigger_source, {
        "hours": hours, "since_hours": since_hours, "limit": limit,
        "with_ai": with_ai, "with_ensemble": with_ensemble,
        "auto_lock": auto_lock, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    return execute_full_workflow(
        run_id,
        hours=hours,
        since_hours=since_hours,
        limit=limit,
        with_ai=with_ai,
        with_ensemble=with_ensemble,
        auto_lock=auto_lock,
        only_missing=only_missing,
        trigger_source=trigger_source,
    )


# ==================== Async workflow variants ====================


async def run_daily_open_workflow_async(
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = False,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "auto_on_open",
) -> int:
    """Async version of run_daily_open_workflow for use within FastAPI async context."""
    run_id = start_workflow_run("daily_open", trigger_source, {
        "hours": hours, "since_hours": since_hours, "limit": limit,
        "with_ai": with_ai, "with_ensemble": with_ensemble,
        "auto_lock": auto_lock, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    try:
        _run_refresh_step(run_id)
        _run_recompute_step(run_id, "post_match_recompute")
        _run_score_step(run_id)
        _update_step(run_id, "pre_match_recompute", "skipped", {"reason": "covered_by_post_match_recompute"})

        if with_ai:
            await _run_ai_prediction_step_async(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        if auto_lock:
            _run_lock_step(run_id, settings.workflow_default_lock_window_hours)
        else:
            _update_step(run_id, "lock_predictions", "skipped", {"reason": "auto_lock=false"})

        _run_accuracy_update_step(run_id)
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"daily_open async workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


async def run_pre_match_workflow_async(
    hours: int = 48,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Async version of run_pre_match_workflow for use within FastAPI async context."""
    run_id = start_workflow_run("pre_match", trigger_source, {
        "hours": hours, "limit": limit, "with_ai": with_ai,
        "with_ensemble": with_ensemble, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    try:
        _update_step(run_id, "refresh_results", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "post_match_recompute", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "post_match_score", "skipped", {"reason": "not_in_pre_match"})
        _run_recompute_step(run_id, "pre_match_recompute")

        if with_ai:
            await _run_ai_prediction_step_async(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        _update_step(run_id, "lock_predictions", "skipped", {"reason": "not_in_pre_match"})
        _update_step(run_id, "accuracy_command_update", "skipped", {"reason": "not_in_pre_match"})
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"pre_match async workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id


async def run_full_workflow_async(
    hours: int = 48,
    since_hours: int = 24,
    limit: int = 10,
    with_ai: bool = True,
    with_ensemble: bool = True,
    auto_lock: bool = True,
    only_missing: bool = True,
    trigger_source: str = "manual_button",
) -> int:
    """Async version of run_full_workflow for use within FastAPI async context."""
    run_id = start_workflow_run("full", trigger_source, {
        "hours": hours, "since_hours": since_hours, "limit": limit,
        "with_ai": with_ai, "with_ensemble": with_ensemble,
        "auto_lock": auto_lock, "only_missing": only_missing,
    })
    if run_id < 0:
        return -1
    try:
        _run_refresh_step(run_id)
        _run_recompute_step(run_id, "post_match_recompute")
        _run_score_step(run_id)
        _update_step(run_id, "pre_match_recompute", "skipped", {"reason": "covered_by_post_match_recompute"})

        if with_ai:
            await _run_ai_prediction_step_async(run_id, limit=limit, only_missing=only_missing)
        else:
            _update_step(run_id, "ai_prediction", "skipped", {"reason": "with_ai=false"})

        if with_ensemble:
            _run_ensemble_step(run_id)
        else:
            _update_step(run_id, "ensemble_generation", "skipped", {"reason": "with_ensemble=false"})

        if auto_lock:
            _run_lock_step(run_id, settings.workflow_default_lock_window_hours)
        else:
            _update_step(run_id, "lock_predictions", "skipped", {"reason": "auto_lock=false"})

        _run_accuracy_update_step(run_id)
        _run_artifact_step(run_id)

        overall = _determine_overall_status(run_id)
        _finish_run(run_id, overall)
    except Exception as e:
        logger.error(f"full async workflow failed: {e}")
        _finish_run(run_id, "failed", error=str(e))

    return run_id
