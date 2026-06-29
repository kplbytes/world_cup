"""Calibration feedback loop — adjusts model parameters based on observed bias.

Reads calibration buckets from ``compute_calibration`` and suggests
parameter adjustments (e.g. ``favorite_dampening``, ``dynamic_draw`` cap)
to correct systematic bias. Adjustments are persisted to
``data/calibration_feedback.json`` so they survive across recomputes.

Safety constraints:
  - Each parameter can shift at most ``MAX_STEP`` per feedback cycle.
  - Each parameter is clamped to ``[MIN, MAX]`` range.
  - Requires ``MIN_SAMPLE`` total scored matches before producing any
    adjustment (avoids noisy small-sample corrections).

This module does NOT modify Poisson probabilities directly. It only nudges
the config knobs that ``recompute.py`` passes into ``predict_match``.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.calibration import compute_calibration

logger = logging.getLogger(__name__)

_FEEDBACK_PATH = Path("data/calibration_feedback.json")

# Safety constraints — each parameter has a [min, max] allowed range and
# a max per-cycle step. These prevent a single calibration cycle from
# catastrophically changing the model.
_PARAM_BOUNDS: dict[str, tuple[float, float, float]] = {
    # name: (min, max, max_step)
    "favorite_dampening": (0.00, 0.15, 0.02),
    "dynamic_draw_cap_group": (0.08, 0.20, 0.02),
    "dynamic_draw_cap_knockout": (0.03, 0.08, 0.01),
    "market_blend_weight": (0.10, 0.30, 0.02),
}

_MIN_TOTAL_SAMPLES = 15  # need at least 15 scored matches for feedback


def _load_current() -> dict[str, float]:
    """Load persisted parameter overrides from disk."""
    if not _FEEDBACK_PATH.exists():
        return {}
    try:
        return {k: float(v) for k, v in json.loads(_FEEDBACK_PATH.read_text()).items()}
    except Exception:
        return {}


def _save(current: dict[str, float]) -> None:
    """Persist parameter overrides to disk."""
    try:
        _FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        _FEEDBACK_PATH.write_text(json.dumps(current, indent=2, sort_keys=True))
    except Exception as exc:
        logger.warning("Failed to persist calibration feedback: %s", exc)


def _compute_target_adjustments(buckets: list[dict[str, Any]]) -> dict[str, float]:
    """Given calibration buckets, compute desired parameter deltas.

    Logic:
      - If high-probability buckets (60-70%, 70%+) have positive gap
        (model overconfident) → increase favorite_dampening.
      - If draw bucket shows actual draw rate > predicted → increase
        dynamic_draw cap.
      - If market Brier is notably better than model → increase
        market_blend_weight (we don't have Brier here, so skip).
    """
    deltas: dict[str, float] = {}

    total_samples = sum(b.get("sample_count", 0) for b in buckets)
    if total_samples < _MIN_TOTAL_SAMPLES:
        logger.info(
            "Calibration feedback skipped: only %d samples (need %d)",
            total_samples, _MIN_TOTAL_SAMPLES,
        )
        return {}

    # --- favorite_dampening: check high-probability buckets for overconfidence ---
    high_buckets = [
        b for b in buckets
        if b.get("label", "") in ("60-70%", "70%+") and b.get("sample_count", 0) >= 3
    ]
    if high_buckets:
        avg_gap = sum(b["calibration_gap"] for b in high_buckets) / len(high_buckets)
        if avg_gap > 0.03:
            # Model overconfident → increase dampening
            deltas["favorite_dampening"] = min(0.02, avg_gap * 0.5)
            logger.info(
                "Calibration feedback: high-bucket gap=%.3f → favorite_dampening += %.3f",
                avg_gap, deltas["favorite_dampening"],
            )
        elif avg_gap < -0.03:
            # Model underconfident → decrease dampening
            deltas["favorite_dampening"] = max(-0.02, avg_gap * 0.5)
            logger.info(
                "Calibration feedback: high-bucket gap=%.3f → favorite_dampening += %.3f",
                avg_gap, deltas["favorite_dampening"],
            )

    # --- dynamic_draw cap: check if draw is underpredicted ---
    # Look at the 30-40% bucket — if predicted avg < actual win rate,
    # it suggests the model is assigning too little to draws.
    low_bucket = next(
        (b for b in buckets if b.get("label") == "30-40%" and b.get("sample_count", 0) >= 3),
        None,
    )
    if low_bucket and low_bucket["calibration_gap"] < -0.03:
        # Actual outcomes more frequent than predicted at this level →
        # likely underdogs/draws are underweighted → loosen draw cap.
        delta = min(0.02, abs(low_bucket["calibration_gap"]) * 0.5)
        deltas["dynamic_draw_cap_group"] = delta
        logger.info(
            "Calibration feedback: low-bucket gap=%.3f → dynamic_draw_cap_group += %.3f",
            low_bucket["calibration_gap"], delta,
        )

    return deltas


def compute_and_persist_feedback(session: Session) -> dict[str, float]:
    """Run calibration feedback and persist parameter overrides.

    Returns the full set of current parameter overrides (after this cycle).
    Safe to call on every recompute — early-tournament no-ops are cheap.
    """
    buckets = compute_calibration(session)
    if not buckets:
        return _load_current()

    target_deltas = _compute_target_adjustments(buckets)
    if not target_deltas:
        # No adjustments this cycle — keep existing overrides
        return _load_current()

    current = _load_current()

    for param, delta in target_deltas.items():
        lo, hi, max_step = _PARAM_BOUNDS.get(param, (0.0, 1.0, 0.01))
        old_val = current.get(param, 0.0)
        # Clamp delta to max_step
        delta = max(-max_step, min(max_step, delta))
        new_val = max(lo, min(hi, old_val + delta))
        current[param] = round(new_val, 4)
        logger.info(
            "Calibration feedback: %s %.4f → %.4f (delta=%.4f, clamped to [%.2f, %.2f])",
            param, old_val, new_val, delta, lo, hi,
        )

    _save(current)
    return current


def get_feedback_overrides() -> dict[str, float]:
    """Read persisted calibration feedback overrides (no DB access).

    Called by ``recompute.py`` when building model configs.
    """
    return _load_current()
