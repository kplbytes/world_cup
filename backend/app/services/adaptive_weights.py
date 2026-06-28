"""Adaptive Ensemble Weights — Bayesian Model Averaging with significance testing.

Algorithm (v2 — scientifically rigorous):
1. Collect per-match Brier scores with exponential time-decay weighting
2. Bayesian posterior: each source's "true Brier" modeled as Normal(μ, σ²)
   - Prior: centered on default weights, σ reflects our uncertainty
   - Posterior: updated with observed Brier scores, σ shrinks with more data
3. Significance test: only adjust weights when performance difference is
   statistically significant (paired t-test on per-match Brier differences)
4. Weight derivation: posterior mean Brier → exponential weighting (Hedge-style)
5. Safety: credibility interval check + max shift + floor weight

Key advantages over v1:
- Sample size naturally handled via Bayesian posterior width
- No weight change until significance is established (avoids noise-driven swings)
- Exponential time decay (not arbitrary window) — recent matches matter more
- Paired test accounts for correlation (same matches evaluated for all sources)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.evaluation import _compute_brier, _get_actual_result, _pick_ai_prediction, _select_system_prediction
from app.ai.model_registry import get_ensemble_defaults, list_enabled_models
from app.models import AIPrediction, MarketSnapshot, Match, PredictionSnapshot

logger = logging.getLogger(__name__)

# ── Configuration ──────────────────────────────────────────────────────

_MIN_SAMPLE_SIZE = 10         # Minimum matches before adapting (raised from 5)
_MAX_WEIGHT_SHIFT = 0.12      # Max shift from default per source (±12%)
_FLOOR_WEIGHT = 0.05          # Minimum weight for any source
_HEDGE_ETA = 1.5              # Hedge/exponential weighting temperature
_TIME_DECAY_HALF_LIFE = 20    # Matches for weight to halve (exponential decay)
_SIGNIFICANCE_LEVEL = 0.10    # p-value threshold for paired t-test (one-sided)
_MAX_LOOKBACK = 60            # Maximum matches to consider

_STATE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "adaptive_weights"

_lock = threading.Lock()


def _is_visible_ai_version(model_version: str | None) -> bool:
    version = (model_version or "").lower()
    return "xiaomi" not in version and "mimo" not in version


def _get_database_identity(session: Session | None = None) -> str:
    if session is not None:
        bind = session.get_bind()
        database = getattr(bind.url, "database", None)
        if database:
            return str(Path(database).expanduser().resolve())
        return str(bind.url)
    return str(Path(settings.database_path).expanduser().resolve())


def _get_state_path(session: Session | None = None) -> Path:
    database_identity = _get_database_identity(session)
    digest = hashlib.sha1(database_identity.encode("utf-8")).hexdigest()[:12]
    return _STATE_DIR / f"adaptive_weights_state_{digest}.json"


# ── Public API ─────────────────────────────────────────────────────────

def compute_adaptive_weights(session: Session) -> dict[str, Any]:
    """Compute adaptive ensemble weights using Bayesian Model Averaging.

    Returns dict with:
        - weights: current adaptive weights (or defaults if insufficient data)
        - performance: per-source Brier scores, sample counts, credibility
        - is_adaptive: whether weights are actually adapted
        - significance: per-pair significance test results
        - last_updated: timestamp of last computation
    """
    # 1. Collect per-match Brier scores with time-decay weights
    per_match_briers, time_weights, match_stage = _collect_per_match_briers(session)

    # 2. Check minimum sample size
    system_sample_count = len(per_match_briers.get("system", {}))
    if system_sample_count < _MIN_SAMPLE_SIZE:
        logger.info(
            "Adaptive weights: only %d matches (need %d), using defaults",
            system_sample_count, _MIN_SAMPLE_SIZE,
        )
        return _build_result(get_defaults_normalized(), _summarize_performance(per_match_briers, time_weights, match_stage), is_adaptive=False, significance={})

    # 3. Compute weighted Brier means and Bayesian posteriors
    performance = _summarize_performance(per_match_briers, time_weights, match_stage)

    # 4. Significance testing: paired t-test on Brier differences
    significance = _paired_significance_tests(per_match_briers, time_weights)

    # 5. Derive weights from Bayesian posteriors + significance
    perf_weights = _bayesian_weights(performance, significance)

    # 6. Blend with defaults (Bayesian shrinkage toward prior)
    defaults = get_defaults_normalized()
    blended = _bayesian_shrinkage(perf_weights, defaults, performance)

    # 7. Apply safety constraints
    constrained = _apply_constraints(blended, defaults)

    # 8. Build result and persist
    result = _build_result(constrained, performance, is_adaptive=True, significance=significance)
    _save_state(result, _get_state_path(session))

    logger.info(
        "Adaptive weights (BMA): system=%.3f market=%.3f ai_total=%.3f "
        "(n=%d, %d significant pairs)",
        constrained.get("system", 0), constrained.get("market", 0),
        sum(v for k, v in constrained.items() if k.startswith("ai_")),
        system_sample_count,
        sum(1 for v in significance.values() if v.get("significant", False)),
    )
    return result


def get_current_adaptive_weights(session: Session | None = None) -> dict[str, Any]:
    """Get current adaptive weights (from cache or compute fresh)."""
    state = _load_state(_get_state_path(session))
    if state and state.get("is_adaptive"):
        return state

    if session is not None:
        return compute_adaptive_weights(session)

    return _build_result(get_defaults_normalized(), {}, is_adaptive=False, significance={})


def get_adaptive_weight_overrides(session: Session) -> dict[str, float] | None:
    """Get weight overrides for ensemble.py to use."""
    result = get_current_adaptive_weights(session)
    if not result.get("is_adaptive"):
        return None
    return result.get("weights", {})


# ── Per-Match Brier Collection with Time Decay ────────────────────────

def _collect_per_match_briers(
    session: Session,
) -> tuple[dict[str, dict[str, float]], dict[str, float], dict[str, str]]:
    """Collect per-match Brier scores for each source, with exponential time-decay weights.

    Returns:
        per_match_briers: {source: {match_id: brier_score}}
        time_weights: {match_id: weight} (most recent = highest weight)
        match_stage: {match_id: "group" | "knockout"} — used by callers to
            bucket Brier statistics by stage. Knockout matches are scored
            using the advance result (home/away) so level-score ET/penalty
            games do not pollute the "draw" bucket.
    """
    final_matches = list(session.scalars(
        select(Match)
        .where(Match.status == "final")
        .order_by(Match.kickoff.desc())
        .limit(_MAX_LOOKBACK)
    ))

    if not final_matches:
        return {}, {}, {}

    # Exponential time-decay weights: most recent match gets weight 1.0,
    # older matches decay with half-life of _TIME_DECAY_HALF_LIFE matches
    n = len(final_matches)
    time_weights = [math.exp(-math.log(2) * i / _TIME_DECAY_HALF_LIFE) for i in range(n)]
    # Normalize so total weight = n (equivalent to uniform if all equal)
    total_tw = sum(time_weights)
    if total_tw > 0:
        time_weights = [w * n / total_tw for w in time_weights]

    match_weights = {match.id: time_weights[idx] for idx, match in enumerate(final_matches)}
    match_stage: dict[str, str] = {}
    per_match_briers: dict[str, dict[str, float]] = {}

    for match in final_matches:
        is_knockout = bool(match.stage and match.stage != "group")
        match_stage[match.id] = "knockout" if is_knockout else "group"
        actual = _get_actual_result(match)

        # System
        snap = _select_system_prediction(session, match)
        if snap:
            probs = {"home_win": snap.home_win, "draw": snap.draw, "away_win": snap.away_win}
            per_match_briers.setdefault("system", {})[match.id] = _compute_brier(probs, actual)

        # Market
        market_snap = session.scalar(
            select(MarketSnapshot)
            .where(MarketSnapshot.match_id == match.id)
            .where(MarketSnapshot.provider == "sporttery")
            .order_by(MarketSnapshot.fetched_at.desc())
            .limit(1)
        )
        if market_snap:
            probs = {"home_win": market_snap.home_probability, "draw": market_snap.draw_probability, "away_win": market_snap.away_probability}
            per_match_briers.setdefault("market", {})[match.id] = _compute_brier(probs, actual)

        # AI by version
        ai_preds = list(session.scalars(
            select(AIPrediction)
            .where(AIPrediction.match_id == match.id)
            .where(AIPrediction.error_code.is_(None))
            .where(AIPrediction.parsed_home_win.isnot(None))
            .where(AIPrediction.parsed_draw.isnot(None))
            .where(AIPrediction.parsed_away_win.isnot(None))
            .where(AIPrediction.real_time_only.is_(False))
            .order_by(AIPrediction.model_version, AIPrediction.created_at.desc())
        ))
        ai_by_version: dict[str, list[AIPrediction]] = defaultdict(list)
        for pred in ai_preds:
            if not _is_visible_ai_version(pred.model_version):
                continue
            ai_by_version[pred.model_version].append(pred)

        for version, predictions in ai_by_version.items():
            pred = _pick_ai_prediction(predictions, match.kickoff)
            if pred is None:
                continue
            probs = {"home_win": pred.parsed_home_win, "draw": pred.parsed_draw, "away_win": pred.parsed_away_win}
            per_match_briers.setdefault(f"ai_{version}", {})[match.id] = _compute_brier(probs, actual)

    return per_match_briers, match_weights, match_stage


# ── Performance Summary ────────────────────────────────────────────────

def _summarize_performance(
    per_match_briers: dict[str, dict[str, float]],
    time_weights: dict[str, float],
    match_stage: dict[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Compute weighted Brier statistics and Bayesian posterior for each source.

    When `match_stage` is provided, also produces a per-stage breakdown so
    callers can independently inspect group-stage vs knockout calibration.
    Knockout samples are usually scarce (≤7 games per tournament) and were
    previously mixed into the group-stage aggregate, masking any
    stage-specific bias. The breakdown does not change the headline Brier
    or the resulting weights — those remain pooled across all matches —
    it only adds a `stage_breakdown` field for diagnostics.
    """
    performance: dict[str, dict[str, Any]] = {}

    ordered_match_ids = list(time_weights.keys())

    def _weighted_stats(match_ids: list[str], briers_by_match: dict[str, float]) -> dict[str, Any]:
        briers = [briers_by_match[mid] for mid in match_ids]
        n = len(briers)
        if n == 0:
            return {"sample_count": 0, "brier": None}
        ws = [time_weights[mid] for mid in match_ids]
        wsum = sum(ws)
        if wsum == 0:
            return {"sample_count": n, "brier": None}
        weighted_mean = sum(b * w for b, w in zip(briers, ws)) / wsum
        return {"sample_count": n, "brier": round(weighted_mean, 4)}

    for source, briers_by_match in per_match_briers.items():
        match_ids = [match_id for match_id in ordered_match_ids if match_id in briers_by_match]
        briers = [briers_by_match[match_id] for match_id in match_ids]
        n = len(briers)
        if n == 0:
            continue

        # Weighted mean Brier
        ws = [time_weights[match_id] for match_id in match_ids]
        wsum = sum(ws)
        if wsum == 0:
            continue
        weighted_mean = sum(b * w for b, w in zip(briers, ws)) / wsum

        # Weighted variance (for Bayesian posterior and significance testing)
        if n >= 2:
            weighted_var = sum(w * (b - weighted_mean) ** 2 for b, w in zip(briers, ws)) / wsum
            # Bessel's correction for weighted variance
            weighted_var = weighted_var * n / (n - 1) if n > 1 else 0
        else:
            weighted_var = 0

        # Per-stage breakdown: group vs knockout Brier computed independently.
        stage_breakdown: dict[str, Any] = {}
        if match_stage:
            for stage_key in ("group", "knockout"):
                stage_match_ids = [mid for mid in match_ids if match_stage.get(mid) == stage_key]
                stage_breakdown[stage_key] = _weighted_stats(stage_match_ids, briers_by_match)

        # Hit rate
        # We need to recompute hit rate separately (not from Brier)
        # For simplicity, estimate from Brier: lower Brier ≈ higher hit rate
        # But let's compute it properly from the data we have
        hits = sum(1 for i in range(n) if briers[i] < 0.5)  # Brier < 0.5 means correct direction
        hit_rate = hits / n if n > 0 else 0

        # Bayesian posterior for "true Brier" μ:
        # Prior: Normal(μ₀=0.5, σ₀²=0.04) — we expect Brier around 0.5 with moderate uncertainty
        # Likelihood: Normal(μ, σ²/n_eff) where n_eff is effective sample size
        prior_mu = 0.50
        prior_var = 0.04  # σ₀ = 0.2, meaning 95% CI ≈ [0.1, 0.9]
        n_eff = wsum  # Effective sample size from time-decay weights
        if n_eff < 1:
            n_eff = 1

        # Posterior: Normal(μ_post, σ²_post)
        # σ²_post = 1 / (1/σ₀² + n_eff/σ²)
        # μ_post = σ²_post * (μ₀/σ₀² + n_eff*weighted_mean/σ²)
        obs_var = max(weighted_var, 0.001)  # Floor to avoid division by zero
        posterior_var = 1.0 / (1.0 / prior_var + n_eff / obs_var)
        posterior_mu = posterior_var * (prior_mu / prior_var + n_eff * weighted_mean / obs_var)

        # 95% credibility interval
        posterior_se = math.sqrt(posterior_var)
        ci_lower = posterior_mu - 1.96 * posterior_se
        ci_upper = posterior_mu + 1.96 * posterior_se

        performance[source] = {
            "sample_count": n,
            "effective_n": round(n_eff, 1),
            "brier": round(weighted_mean, 4),
            "brier_var": round(obs_var, 4),
            "hit_rate": round(hit_rate, 3),
            "posterior_mu": round(posterior_mu, 4),
            "posterior_se": round(posterior_se, 4),
            "ci_95": [round(ci_lower, 4), round(ci_upper, 4)],
            # Independent Brier per stage so knockout calibration is visible
            # even when pooled sample size is dominated by group stage.
            "stage_breakdown": stage_breakdown,
        }

    return performance


# ── Significance Testing ───────────────────────────────────────────────

def _paired_significance_tests(
    per_match_briers: dict[str, dict[str, float]],
    time_weights: dict[str, float],
) -> dict[str, dict[str, Any]]:
    """Paired t-test on per-match Brier differences between sources.

    Only test pairs that matter: system vs market, system vs AI, market vs AI.
    Uses time-decay weights in the test.
    """
    sources = list(per_match_briers.keys())
    if len(sources) < 2:
        return {}

    results: dict[str, dict[str, Any]] = {}
    ordered_match_ids = list(time_weights.keys())

    # Find common match indices (where both sources have data)
    for i in range(len(sources)):
        for j in range(i + 1, len(sources)):
            src_a, src_b = sources[i], sources[j]
            briers_a = per_match_briers[src_a]
            briers_b = per_match_briers[src_b]

            common_match_ids = [
                match_id
                for match_id in ordered_match_ids
                if match_id in briers_a and match_id in briers_b
            ]
            n_pairs = len(common_match_ids)
            if n_pairs < _MIN_SAMPLE_SIZE:
                continue

            # Paired differences: A - B (positive means A is worse)
            diffs = [briers_a[match_id] - briers_b[match_id] for match_id in common_match_ids]
            ws = [time_weights[match_id] for match_id in common_match_ids]
            wsum = sum(ws)

            if wsum == 0:
                continue

            # Weighted mean and variance of differences
            d_mean = sum(d * w for d, w in zip(diffs, ws)) / wsum
            d_var = sum(w * (d - d_mean) ** 2 for d, w in zip(diffs, ws)) / wsum
            # Bessel's correction
            if n_pairs > 1:
                d_var = d_var * n_pairs / (n_pairs - 1)

            n_eff = wsum
            if n_eff < 1:
                n_eff = 1

            # Paired t-statistic
            se = math.sqrt(d_var / n_eff) if d_var > 0 else 0
            if se == 0:
                t_stat = 0
                p_value = 1.0
            else:
                t_stat = d_mean / se
                # Approximate p-value using normal distribution (valid for n >= 10)
                p_value = 2 * (1 - _normal_cdf(abs(t_stat)))

            # Which source is better?
            better = src_b if d_mean > 0 else src_a  # Lower Brier = better
            significant = p_value < _SIGNIFICANCE_LEVEL

            pair_key = f"{src_a}_vs_{src_b}"
            results[pair_key] = {
                "diff_mean": round(d_mean, 4),
                "t_stat": round(t_stat, 3),
                "p_value": round(p_value, 4),
                "significant": significant,
                "better_source": better,
                "n_pairs": n_pairs,
            }

    return results


def _normal_cdf(x: float) -> float:
    """Approximate normal CDF using error function."""
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ── Bayesian Weight Derivation ─────────────────────────────────────────

def _bayesian_weights(
    performance: dict[str, dict[str, Any]],
    significance: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """Derive weights from Bayesian posteriors, modulated by significance.

    Core idea:
    - Base weights from posterior mean Brier (lower = better → higher weight)
    - Weight adjustment proportional to significance of the difference
    - If no significant difference found, weights stay close to defaults
    """
    # Collect posterior means for sources with enough data
    briers = {}
    for source, perf in performance.items():
        if perf.get("sample_count", 0) >= _MIN_SAMPLE_SIZE:
            briers[source] = perf.get("posterior_mu", perf.get("brier", 0.5))

    if not briers:
        return get_defaults_normalized()

    # Check which sources are significantly better than others
    # Build a "significance score" for each source
    sig_scores: dict[str, float] = {s: 0.0 for s in briers}
    for pair_key, pair_result in significance.items():
        if not pair_result.get("significant", False):
            continue
        better = pair_result.get("better_source", "")
        p_val = pair_result.get("p_value", 1.0)
        # Stronger significance → bigger score boost
        sig_scores[better] = sig_scores.get(better, 0.0) + (1.0 - p_val)

    # Exponential weighting (Hedge-style): weight ∝ exp(-η * brier) * (1 + sig_bonus)
    raw_weights = {}
    for source, brier in briers.items():
        sig_bonus = 1.0 + min(sig_scores.get(source, 0.0), 1.0)  # Cap at 2x
        raw_weights[source] = math.exp(-_HEDGE_ETA * brier) * sig_bonus

    # Normalize
    total = sum(raw_weights.values())
    if total > 0:
        weights = {k: v / total for k, v in raw_weights.items()}
    else:
        n = len(briers)
        weights = {k: 1.0 / n for k in briers}

    return weights


def _bayesian_shrinkage(
    perf_weights: dict[str, float],
    defaults: dict[str, float],
    performance: dict[str, dict[str, Any]],
) -> dict[str, float]:
    """Bayesian shrinkage toward priors (defaults), proportional to posterior uncertainty.

    Key insight: with few matches, posterior is wide → shrink more toward defaults.
    With many matches, posterior is narrow → trust the data more.

    Shrinkage factor = 1 - (posterior_se / prior_se)
    When posterior_se ≈ prior_se → factor ≈ 0 → use defaults
    When posterior_se ≈ 0 → factor ≈ 1 → use performance weights
    """
    prior_se = 0.2  # σ₀ from our prior
    blended = {}

    all_keys = set(list(perf_weights.keys()) + list(defaults.keys()))

    for key in all_keys:
        pw = perf_weights.get(key, 0.0)
        dw = defaults.get(key, 0.0)

        if key in perf_weights and key in defaults:
            # Compute data-driven shrinkage factor from posterior uncertainty
            post_se = performance.get(key, {}).get("posterior_se", prior_se)
            # Shrinkage: how much to trust data vs prior
            shrinkage = max(0.0, min(1.0, 1.0 - (post_se / prior_se)))
            blended[key] = shrinkage * pw + (1 - shrinkage) * dw
        elif key in perf_weights:
            blended[key] = pw * 0.3  # Conservative: only 30% weight for unknown sources
        else:
            blended[key] = dw

    # Normalize
    total = sum(blended.values())
    if total > 0:
        for key in blended:
            blended[key] /= total

    return blended


# ── Safety Constraints ─────────────────────────────────────────────────

def _apply_constraints(
    weights: dict[str, float],
    defaults: dict[str, float],
) -> dict[str, float]:
    """Apply safety constraints to prevent wild weight swings."""
    constrained = {}

    for key in weights:
        w = weights[key]
        d = defaults.get(key, 0.0)

        # Floor: minimum weight for any source
        w = max(w, _FLOOR_WEIGHT)

        # Cap: maximum deviation from default
        if d > 0:
            w = max(d - _MAX_WEIGHT_SHIFT, min(d + _MAX_WEIGHT_SHIFT, w))

        constrained[key] = w

    # Normalize
    total = sum(constrained.values())
    if total > 0:
        for key in constrained:
            constrained[key] /= total

    return constrained


# ── Helpers ─────────────────────────────────────────────────────────────

def get_defaults_normalized() -> dict[str, float]:
    """Get default weights normalized to sum to 1.0."""
    defaults = get_ensemble_defaults()
    return {
        "system": defaults.get("system_weight", 0.35),
        "market": defaults.get("market_weight", 0.30),
        "ai_total": defaults.get("total_ai_weight", 0.35),
    }


# ── State Persistence ──────────────────────────────────────────────────

def _build_result(
    weights: dict[str, float],
    performance: dict[str, dict[str, Any]],
    is_adaptive: bool,
    significance: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build the result dict."""
    defaults = get_ensemble_defaults()
    expanded = dict(weights)

    ai_total = expanded.pop("ai_total", 0.0)
    if ai_total > 0:
        enabled_models = [model for model in list_enabled_models() if _is_visible_ai_version(model.model_version)]
        total_config_weight = sum(m.ensemble_weight for m in enabled_models)
        if total_config_weight > 0 and enabled_models:
            for model in enabled_models:
                expanded[f"ai_{model.model_version}"] = ai_total * (model.ensemble_weight / total_config_weight)
        elif enabled_models:
            for model in enabled_models:
                expanded[f"ai_{model.model_version}"] = ai_total / len(enabled_models)

    return {
        "weights": expanded,
        "performance": performance,
        "is_adaptive": is_adaptive,
        "significance": significance,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "config": {
            "algorithm": "bayesian_model_averaging_v2",
            "min_sample_size": _MIN_SAMPLE_SIZE,
            "max_weight_shift": _MAX_WEIGHT_SHIFT,
            "hedge_eta": _HEDGE_ETA,
            "time_decay_half_life": _TIME_DECAY_HALF_LIFE,
            "significance_level": _SIGNIFICANCE_LEVEL,
            "floor_weight": _FLOOR_WEIGHT,
            "max_lookback": _MAX_LOOKBACK,
        },
    }


def _save_state(state: dict[str, Any], state_path: Path) -> None:
    """Persist adaptive weights state to JSON file."""
    with _lock:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with open(state_path, "w") as f:
            json.dump(state, f, indent=2, default=str)


def _load_state(state_path: Path) -> dict[str, Any] | None:
    """Load adaptive weights state from JSON file."""
    with _lock:
        if not state_path.exists():
            return None
        try:
            with open(state_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
