"""Workflow request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class DailyOpenRequest(BaseModel):
    hours: int = 48
    since_hours: int = 24
    limit: int = 10
    with_ai: bool = False
    with_ensemble: bool = True
    auto_lock: bool = True
    only_missing: bool = True


class PreMatchRequest(BaseModel):
    hours: int = 48
    limit: int = 10
    with_ai: bool = True
    with_ensemble: bool = True
    only_missing: bool = True


class PostMatchRequest(BaseModel):
    since_hours: int = 24


class LockRequest(BaseModel):
    window_hours: int = 24


class FullWorkflowRequest(BaseModel):
    hours: int = 48
    since_hours: int = 24
    limit: int = 10
    with_ai: bool = True
    with_ensemble: bool = True
    auto_lock: bool = True
    only_missing: bool = True


class WorkflowStepStatus(BaseModel):
    step_name: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    summary: dict[str, Any] | None = None
    error_message: str | None = None


class WorkflowRunStatus(BaseModel):
    id: int
    workflow_type: str
    trigger_source: str
    status: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = None
    steps: list[WorkflowStepStatus] = []
    summary: dict[str, Any] | None = None
    error_message: str | None = None


class WorkflowStatusResponse(BaseModel):
    today_status: str  # needs_run / already_run / running
    last_run_at: datetime | None = None
    recommended_action: str  # run_daily_open_workflow / none / etc.
    yesterday_matches: dict[str, Any] = {}
    upcoming_matches: dict[str, Any] = {}
    lock_status: dict[str, Any] = {}
    last_run: WorkflowRunStatus | None = None
