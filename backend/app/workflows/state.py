"""Workflow state management - track running workflows and cooldowns."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.db import session_scope
from app.models import WorkflowRun, WorkflowStep

logger = logging.getLogger(__name__)

_workflow_lock = threading.Lock()
_current_run_id: int | None = None


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure datetime is timezone-aware (UTC). SQLite returns naive datetimes."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_workflow_running() -> bool:
    """Check if a workflow is currently running."""
    with _workflow_lock:
        return _current_run_id is not None


def set_current_run(run_id: int | None) -> None:
    """Set the current running workflow ID."""
    global _current_run_id
    with _workflow_lock:
        _current_run_id = run_id


def get_current_run_id() -> int | None:
    """Get the current running workflow ID."""
    with _workflow_lock:
        return _current_run_id


def try_start_workflow(run_id: int) -> bool:
    """Atomically check and set workflow running state. Returns True if successfully started."""
    global _current_run_id
    with _workflow_lock:
        if _current_run_id is not None:
            return False
        _current_run_id = run_id
        return True


def can_auto_run() -> bool:
    """Check if auto-run is allowed (cooldown check)."""
    with session_scope() as session:
        last_run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.trigger_source == "auto_on_open")
            .where(WorkflowRun.status.in_(["success", "partial_success"]))
            .order_by(WorkflowRun.started_at.desc())
            .limit(1)
        )
        if last_run is None:
            return True
        cooldown = timedelta(minutes=settings.workflow_auto_run_cooldown_minutes)
        started = _ensure_utc(last_run.started_at)
        return datetime.now(timezone.utc) - started > cooldown


def get_today_status() -> dict:
    """Get today's workflow status.

    Returns a dict with:
      - status: "already_run" | "partial_success" | "running" | "needs_run"
      - failed_steps: list of step names that failed (only when status == "partial_success")
    """
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    with session_scope() as session:
        today_run = session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.started_at >= today_start)
            .where(WorkflowRun.status.in_(["success", "partial_success"]))
            .order_by(WorkflowRun.started_at.desc())
            .limit(1)
        )
        if today_run:
            result_status = "partial_success" if today_run.status == "partial_success" else "already_run"
            failed_steps = []
            if result_status == "partial_success":
                steps = list(session.scalars(
                    select(WorkflowStep)
                    .where(WorkflowStep.workflow_run_id == today_run.id)
                    .where(WorkflowStep.status == "failed")
                ))
                failed_steps = [
                    {"step_name": s.step_name, "error_message": s.error_message}
                    for s in steps
                ]
            return {
                "status": result_status,
                "failed_steps": failed_steps,
            }
        if is_workflow_running():
            return {"status": "running", "failed_steps": []}
        return {"status": "needs_run", "failed_steps": []}
