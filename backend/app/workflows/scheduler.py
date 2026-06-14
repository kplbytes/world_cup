"""Lightweight auto-trigger logic for local workflow."""

from __future__ import annotations

import logging

from app.config import settings
from app.workflows.state import can_auto_run, get_today_status, is_workflow_running

logger = logging.getLogger(__name__)


def should_auto_run_daily() -> bool:
    """Check if daily workflow should auto-run on page open."""
    if not settings.auto_run_daily_workflow_on_open:
        return False
    if is_workflow_running():
        return False
    if get_today_status() == "already_run":
        return False
    return can_auto_run()
