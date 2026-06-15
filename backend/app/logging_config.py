"""Centralized logging configuration for the World Cup prediction system.

Features:
- Structured JSON log format with timestamp, level, module, request_id
- Log rotation by size (10MB) with time-based backup (30 days retention)
- Per-module log level configuration
- Request ID context for tracing operation chains
- Dual output: console (human-readable) + file (JSON structured)
"""

import json
import logging
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import PROJECT_ROOT

# ---------------------------------------------------------------------------
# Context variables for request tracing
# ---------------------------------------------------------------------------

request_id_var: ContextVar[str] = ContextVar("request_id", default="")
workflow_run_id_var: ContextVar[str] = ContextVar("workflow_run_id", default="")

# ---------------------------------------------------------------------------
# Log directory
# ---------------------------------------------------------------------------

LOG_DIR = PROJECT_ROOT / "data" / "logs"


def _ensure_log_dir() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# JSON Formatter
# ---------------------------------------------------------------------------


class StructuredJsonFormatter(logging.Formatter):
    """Format log records as JSON lines for machine parsing."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add context variables
        req_id = request_id_var.get("")
        if req_id:
            log_entry["request_id"] = req_id

        wf_id = workflow_run_id_var.get("")
        if wf_id:
            log_entry["workflow_run_id"] = wf_id

        # Add location info
        log_entry["module"] = record.module
        log_entry["func"] = record.funcName
        log_entry["line"] = record.lineno

        # Add exception info
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
            log_entry["exception_type"] = record.exc_info[0].__name__

        # Add any extra fields
        for key in ("match_id", "provider", "model_version", "duration_ms", "step"):
            value = getattr(record, key, None)
            if value is not None:
                log_entry[key] = value

        return json.dumps(log_entry, ensure_ascii=False)


class ConsoleFormatter(logging.Formatter):
    """Human-readable console format with request_id when available."""

    def format(self, record: logging.LogRecord) -> str:
        req_id = request_id_var.get("")
        wf_id = workflow_run_id_var.get("")
        context_parts = []
        if req_id:
            context_parts.append(f"req={req_id[:8]}")
        if wf_id:
            context_parts.append(f"wf={wf_id[:8]}")
        context = f" [{','.join(context_parts)}]" if context_parts else ""

        fmt = f"%(asctime)s %(levelname)-5s %(name)s{context} %(message)s"
        formatter = logging.Formatter(fmt, datefmt="%H:%M:%S")
        return formatter.format(record)


# ---------------------------------------------------------------------------
# Per-module log level configuration
# ---------------------------------------------------------------------------

MODULE_LOG_LEVELS: dict[str, int] = {
    # Core services: INFO
    "app.services.recompute": logging.INFO,
    "app.services.snapshots": logging.INFO,
    "app.services.scoring": logging.INFO,
    "app.services.dashboard": logging.INFO,
    "app.workflows.service": logging.INFO,
    # AI providers: INFO (reduce noise from HTTP retries)
    "app.ai.service": logging.INFO,
    "app.ai.ensemble": logging.INFO,
    "app.ai.provider_registry": logging.INFO,
    "app.ai.providers.openai_compat": logging.INFO,
    # Intelligence providers: INFO
    "app.intelligence.pipeline": logging.INFO,
    "app.intelligence.providers.api_football": logging.INFO,
    "app.intelligence.providers.sportmonks": logging.INFO,
    "app.intelligence.providers.sporttery": logging.INFO,
    # Tournament: WARNING (bracket allocation is noisy during group stage)
    "app.tournament.qualification": logging.WARNING,
    "app.tournament.bracket": logging.WARNING,
    # Database: WARNING
    "app.db": logging.WARNING,
    # Third-party: WARNING
    "apscheduler": logging.WARNING,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "urllib3": logging.WARNING,
    "openai": logging.WARNING,
}


# ---------------------------------------------------------------------------
# Setup function
# ---------------------------------------------------------------------------


def setup_logging(
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    json_file: bool = True,
) -> None:
    """Initialize the centralized logging system.

    Args:
        console_level: Minimum level for console output.
        file_level: Minimum level for file output.
        json_file: If True, write JSON-structured logs to file.
    """
    _ensure_log_dir()

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # Remove existing handlers to avoid duplicates
    root_logger.handlers.clear()

    # 1. Console handler (human-readable)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_level)
    console_handler.setFormatter(ConsoleFormatter())
    root_logger.addHandler(console_handler)

    # 2. File handler (JSON structured, size-based rotation)
    if json_file:
        json_handler = RotatingFileHandler(
            LOG_DIR / "app.jsonl",
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=30,  # Keep 30 rotated files (~300MB max)
            encoding="utf-8",
        )
        json_handler.setLevel(file_level)
        json_handler.setFormatter(StructuredJsonFormatter())
        root_logger.addHandler(json_handler)

    # 3. Error file handler (ERROR+ only, for quick problem identification)
    error_handler = RotatingFileHandler(
        LOG_DIR / "error.jsonl",
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=10,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(StructuredJsonFormatter())
    root_logger.addHandler(error_handler)

    # 4. Apply per-module log levels
    for module_name, level in MODULE_LOG_LEVELS.items():
        logging.getLogger(module_name).setLevel(level)


def generate_request_id() -> str:
    """Generate a new request ID and set it in context."""
    rid = uuid.uuid4().hex[:16]
    request_id_var.set(rid)
    return rid


def set_workflow_context(run_id: str | int) -> None:
    """Set workflow run ID in logging context."""
    workflow_run_id_var.set(str(run_id))


def clear_workflow_context() -> None:
    """Clear workflow run ID from logging context."""
    workflow_run_id_var.set("")
