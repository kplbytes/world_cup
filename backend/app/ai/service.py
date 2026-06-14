"""AI Prediction Service - orchestrate AI prediction generation, storage, and querying."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.model_registry import (
    get_ensemble_defaults,
    get_model_config,
    get_provider_config,
    list_enabled_models,
)
from app.ai.lock_status import compute_match_lock_status
from app.ai.parser import parse_ai_response
from app.ai.prompt_builder import build_prompt
from app.ai.providers.base import AIPredictionRequest, AIModelConfig
from app.ai.schemas import AIParsedOutput
from app.config import settings
from app.models import AIPrediction, Match, MarketSnapshot, PredictionSnapshot

logger = logging.getLogger(__name__)


def is_ai_enabled() -> bool:
    """Check if AI prediction is globally enabled."""
    return settings.enable_ai_prediction


def get_ai_run_mode() -> str:
    """Get the AI run mode (manual or auto)."""
    return settings.ai_run_mode


def get_prompt_version() -> str:
    """Get the current prompt version."""
    return settings.ai_prompt_version


def _find_existing_prediction(session: Session, match_id: str, model_version: str, prompt_version: str | None = None) -> AIPrediction | None:
    """Find an existing successful AI prediction for dedup."""
    prompt_ver = prompt_version or get_prompt_version()
    return session.scalar(
        select(AIPrediction)
        .where(
            AIPrediction.match_id == match_id,
            AIPrediction.model_version == model_version,
            AIPrediction.prompt_version == prompt_ver,
            AIPrediction.error_code.is_(None),
            AIPrediction.parsed_home_win.isnot(None),
        )
        .order_by(AIPrediction.created_at.desc())
        .limit(1)
    )


async def run_ai_prediction(
    session: Session,
    match_id: str,
    model_version: str,
    force: bool = False,
) -> dict[str, Any]:
    """Run a single AI prediction for a match.

    Convenience wrapper that calls _call_ai_provider then _process_and_save_prediction.
    """
    if not is_ai_enabled():
        return {"status": "disabled", "error": "AI prediction is not enabled"}

    model_config = get_model_config(model_version)
    prompt_ver = model_config.prompt_version if model_config else get_prompt_version()

    if not force:
        existing = _find_existing_prediction(session, match_id, model_version, prompt_ver)
        if existing:
            return {
                "status": "skipped_existing",
                "match_id": match_id,
                "model_version": model_version,
                "prediction_id": existing.id,
                "home_win": existing.parsed_home_win,
                "draw": existing.parsed_draw,
                "away_win": existing.parsed_away_win,
                "confidence": existing.confidence,
                "recommended_label": existing.recommended_label,
            }

    raw_result = await _call_ai_provider(session, match_id, model_version)
    return _process_and_save_prediction(session, match_id, model_version, raw_result)


async def _call_ai_provider(
    session: Session,
    match_id: str,
    model_version: str,
) -> dict[str, Any]:
    """Call a single AI provider and return raw results (no DB writes).

    Returns a dict with the raw response data needed to create an AIPrediction record.
    """
    model_config = get_model_config(model_version)
    if not model_config:
        return {"status": "error", "error": f"Unknown model version: {model_version}"}

    if not model_config.enabled:
        return {"status": "disabled", "error": f"Model {model_version} is disabled"}

    provider_config = get_provider_config(model_config.provider_name)
    if not provider_config:
        return {"status": "error", "error": f"Unknown provider: {model_config.provider_name}"}

    # Build the prediction request
    request = _build_prediction_request(session, match_id)
    if request is None:
        return {"status": "error", "error": f"Cannot build prediction request for {match_id}"}

    # Build the prompt
    # Use model-specific prompt_version if available, otherwise global default
    prompt_ver = model_config.prompt_version or get_prompt_version()
    prompt = build_prompt(request, prompt_ver)

    # Save the input snapshot
    input_snapshot = {
        "match_id": match_id,
        "model_version": model_version,
        "prompt_version": prompt_ver,
        "system_probs": {
            "home_win": request.system_home_win,
            "draw": request.system_draw,
            "away_win": request.system_away_win,
        },
        "market_probs": {
            "home_win": request.market_home_prob,
            "draw": request.market_draw_prob,
            "away_win": request.market_away_prob,
        } if request.market_home_prob else None,
        "home_team_profile": request.home_team_profile,
        "away_team_profile": request.away_team_profile,
    }

    # Get the provider
    provider = _get_provider(provider_config)
    if provider is None:
        return {"status": "error", "error": f"Provider {model_config.provider_name} not implemented"}

    if not provider.is_configured():
        return {"status": "error", "error": f"Provider {model_config.provider_name} not configured (missing API key)"}

    # Call the AI model
    raw_response, error, latency_ms = await provider.predict(model_config, prompt)

    return {
        "status": "api_call_done",
        "match_id": match_id,
        "model_version": model_version,
        "model_config": model_config,
        "input_snapshot": input_snapshot,
        "request": request,
        "raw_response": raw_response,
        "error": error,
        "latency_ms": latency_ms,
    }


def _process_and_save_prediction(
    session: Session,
    match_id: str,
    model_version: str,
    raw_result: dict[str, Any],
) -> dict[str, Any]:
    """Process a raw AI result and save the AIPrediction to DB (serial, no concurrency).

    Args:
        session: SQLAlchemy session
        match_id: match ID
        model_version: model version string
        raw_result: output from _call_ai_provider

    Returns a dict with the result status and data.
    """
    if raw_result.get("status") != "api_call_done":
        # Propagate error/disabled status from the API call phase
        return raw_result

    model_config = raw_result["model_config"]
    input_snapshot = raw_result["input_snapshot"]
    request = raw_result["request"]
    raw_response = raw_result["raw_response"]
    error = raw_result["error"]
    latency_ms = raw_result["latency_ms"]

    # Create the AI prediction record
    ai_pred = AIPrediction(
        match_id=match_id,
        provider=model_config.provider_name,
        model_id=model_config.model_id,
        model_version=model_version,
        prompt_version=input_snapshot["prompt_version"],
        input_snapshot_json=input_snapshot,
        raw_response_text=raw_response,
        latency_ms=latency_ms,
        created_at=datetime.now(timezone.utc),
    )

    if error:
        ai_pred.error_code = error.error_code
        ai_pred.error_message = error.error_message
        session.add(ai_pred)
        session.flush()
        return {
            "status": "error",
            "error_code": error.error_code,
            "error_message": error.error_message,
            "latency_ms": latency_ms,
            "prediction_id": ai_pred.id,
        }

    # Parse the response
    if raw_response:
        ai_pred.raw_response_json = _safe_json_parse(raw_response)
        parsed, warnings = parse_ai_response(raw_response)

        if parsed:
            # Validate probabilities
            prob_sum = parsed.home_win + parsed.draw + parsed.away_win
            prob_valid = True
            prob_warnings = []

            # Check each probability is in [0, 1]
            for name, val in [("home_win", parsed.home_win), ("draw", parsed.draw), ("away_win", parsed.away_win)]:
                if not isinstance(val, (int, float)) or val < 0 or val > 1:
                    prob_valid = False
                    prob_warnings.append(f"{name}={val} out of [0,1]")

            # Check sum is reasonable
            if prob_valid:
                if prob_sum < 0.80 or prob_sum > 1.20:
                    prob_valid = False
                    prob_warnings.append(f"sum={prob_sum:.4f} outside [0.80, 1.20]")
                elif abs(prob_sum - 1.0) <= 0.02:
                    # Close enough to 1.0, normalize
                    parsed.home_win /= prob_sum
                    parsed.draw /= prob_sum
                    parsed.away_win /= prob_sum

            if prob_valid:
                ai_pred.parsed_home_win = parsed.home_win
                ai_pred.parsed_draw = parsed.draw
                ai_pred.parsed_away_win = parsed.away_win
                ai_pred.confidence = parsed.confidence
                ai_pred.risk_flags_json = parsed.risk_flags
                ai_pred.risk_flags_json = list(dict.fromkeys(parsed.risk_flags + parsed.profile_risk_flags))
                ai_pred.key_factors_json = list(dict.fromkeys(parsed.key_factors + parsed.profile_factors))
                ai_pred.reason = parsed.reason
                ai_pred.uncertainties_json = parsed.uncertainties
                ai_pred.disagreement_with_system = parsed.disagreement_with_system
                ai_pred.disagreement_with_market = parsed.disagreement_with_market
                ai_pred.recommended_label = parsed.recommended_label
            else:
                ai_pred.error_code = "invalid_probabilities"
                ai_pred.error_message = "; ".join(prob_warnings)
        else:
            ai_pred.error_code = "parse_failed"
            ai_pred.error_message = "; ".join(warnings[:5])

        # Calculate disagreements
        if parsed and parsed.home_win > 0:
            sys_pred = _get_system_prediction_direction(
                request.system_home_win, request.system_draw, request.system_away_win
            )
            ai_pred_dir = _get_system_prediction_direction(parsed.home_win, parsed.draw, parsed.away_win)
            if sys_pred != ai_pred_dir:
                ai_pred.disagreement_with_system = ai_pred.disagreement_with_system or f"System says {sys_pred}, AI says {ai_pred_dir}"

            if request.market_home_prob:
                mkt_pred = _get_system_prediction_direction(
                    request.market_home_prob, request.market_draw_prob or 0, request.market_away_prob or 0
                )
                if mkt_pred != ai_pred_dir:
                    ai_pred.disagreement_with_market = ai_pred.disagreement_with_market or f"Market says {mkt_pred}, AI says {ai_pred_dir}"

    # Set locking status based on match kickoff
    match = session.get(Match, match_id)
    if match:
        now = datetime.now(timezone.utc)
        lock = compute_match_lock_status(match, now)
        ai_pred.is_pre_match_locked = lock.is_pre_match_locked
        ai_pred.is_fallback_locked = lock.is_fallback_locked
        ai_pred.real_time_only = lock.real_time_only
        ai_pred.locked_at = lock.locked_at

    session.add(ai_pred)
    session.flush()

    return {
        "status": "success",
        "prediction_id": ai_pred.id,
        "model_version": model_version,
        "home_win": ai_pred.parsed_home_win,
        "draw": ai_pred.parsed_draw,
        "away_win": ai_pred.parsed_away_win,
        "confidence": ai_pred.confidence,
        "recommended_label": ai_pred.recommended_label,
        "latency_ms": latency_ms,
        "error_code": ai_pred.error_code,
    }


async def run_ai_predictions_for_match(
    session: Session,
    match_id: str,
    force: bool = False,
) -> list[dict[str, Any]]:
    """Run all enabled AI models for a match.

    Phase 1: Concurrent API calls (no DB writes).
    Phase 2: Serial DB writes to avoid concurrent session access.
    """
    import asyncio

    models = list_enabled_models()
    if not models:
        return []

    # Skip models whose provider is not configured (no API key)
    runnable_models = []
    for model in models:
        provider_config = get_provider_config(model.provider_name)
        if provider_config:
            provider = _get_provider(provider_config)
            if provider and provider.is_configured():
                runnable_models.append(model)
            else:
                logger.info(f"Skipping model {model.model_version}: provider not configured (no API key)")
        else:
            logger.info(f"Skipping model {model.model_version}: no provider config found")

    if not runnable_models:
        return []

    # Dedup: skip models that already have a successful prediction (unless force=True)
    skipped_results: list[dict[str, Any]] = []
    if not force:
        models_to_run = []
        for model in runnable_models:
            existing = _find_existing_prediction(session, match_id, model.model_version, model.prompt_version)
            if existing:
                skipped_results.append({
                    "status": "skipped_existing",
                    "match_id": match_id,
                    "model_version": model.model_version,
                    "prediction_id": existing.id,
                    "home_win": existing.parsed_home_win,
                    "draw": existing.parsed_draw,
                    "away_win": existing.parsed_away_win,
                    "confidence": existing.confidence,
                    "recommended_label": existing.recommended_label,
                })
                logger.info(f"Skipping model {model.model_version} for {match_id}: existing prediction found")
            else:
                models_to_run.append(model)
        runnable_models = models_to_run

    if not runnable_models:
        return skipped_results

    max_concurrent = settings.ai_max_concurrent_requests
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _call_with_semaphore(model):
        async with semaphore:
            try:
                result = await _call_ai_provider(session, match_id, model.model_version)
                return result
            except Exception as e:
                logger.error(f"Error calling {model.model_version} for {match_id}: {e}")
                return {"status": "error", "model_version": model.model_version, "error": str(e)}

    # Phase 1: Concurrent API calls (no DB writes)
    raw_results = await asyncio.gather(
        *[_call_with_semaphore(model) for model in runnable_models],
        return_exceptions=True,
    )

    # Handle any unexpected exceptions from gather
    safe_results = []
    for i, result in enumerate(raw_results):
        if isinstance(result, Exception):
            logger.error(f"Unexpected error for {runnable_models[i].model_version}: {result}")
            safe_results.append({"status": "error", "model_version": runnable_models[i].model_version, "error": str(result)})
        else:
            safe_results.append(result)

    # Phase 2: Serial DB writes
    results = []
    for model, raw_result in zip(runnable_models, safe_results):
        result = _process_and_save_prediction(session, match_id, model.model_version, raw_result)
        results.append(result)

    return skipped_results + results


async def run_ai_predictions_batch(
    session: Session,
    stage: str | None = None,
    limit: int = 10,
    only_missing: bool = True,
    retry_failed: bool = False,
) -> list[dict[str, Any]]:
    """Run AI predictions for multiple matches.

    Args:
        stage: Filter by match stage (group, round_of_16, etc.)
        limit: Maximum number of matches to process
        only_missing: If True, skip matches that already have AI predictions
        retry_failed: If True, also retry models that previously failed
    """
    query = select(Match).where(Match.status != "final")
    if stage:
        query = query.where(Match.stage == stage)
    query = query.order_by(Match.kickoff).limit(limit)
    matches = list(session.scalars(query))

    results = []
    for match in matches:
        if only_missing:
            # Get all enabled model versions
            enabled_versions = {m.model_version for m in list_enabled_models()}

            # Get existing predictions for this match
            existing_preds = list(session.scalars(
                select(AIPrediction)
                .where(AIPrediction.match_id == match.id)
            ))

            # Check which model versions already have predictions
            versions_with_success = set()
            versions_with_error = set()
            for pred in existing_preds:
                if pred.error_code is None and pred.parsed_home_win is not None:
                    versions_with_success.add(pred.model_version)
                elif pred.error_code is not None:
                    versions_with_error.add(pred.model_version)

            # Determine which versions still need predictions
            missing_versions = enabled_versions - versions_with_success

            if retry_failed:
                # Also retry failed versions
                missing_versions = missing_versions | (versions_with_error - versions_with_success)

            if not missing_versions:
                # All models already have predictions
                skipped_versions = enabled_versions - missing_versions
                if skipped_versions:
                    results.append({
                        "status": "skipped",
                        "match_id": match.id,
                        "reason": "all_models_have_predictions",
                        "existing_versions": list(versions_with_success),
                    })
                continue

        match_results = await run_ai_predictions_for_match(session, match.id)
        results.extend(match_results)

    return results


def get_ai_predictions(session: Session, match_id: str) -> list[dict[str, Any]]:
    """Get all AI predictions for a match."""
    rows = session.execute(
        select(AIPrediction)
        .where(AIPrediction.match_id == match_id)
        .order_by(AIPrediction.created_at.desc())
    ).scalars().all()

    # Get baseline system prediction for comparison
    baseline_probs = _get_baseline_probs(session, match_id)

    return [_serialize_ai_prediction(row, baseline_probs) for row in rows]


def list_ai_model_status(session: Session) -> list[dict[str, Any]]:
    """List all configured AI models with their status."""
    from sqlalchemy import func as sqlfunc

    models = list_enabled_models()

    # Aggregate: last success per model_version
    success_subq = (
        select(
            AIPrediction.model_version,
            sqlfunc.max(AIPrediction.created_at).label("last_success_at"),
            sqlfunc.count(AIPrediction.id).label("success_count"),
        )
        .where(AIPrediction.error_code.is_(None))
        .group_by(AIPrediction.model_version)
        .subquery()
    )

    # Aggregate: last error per model_version
    error_subq = (
        select(
            AIPrediction.model_version,
            sqlfunc.max(AIPrediction.created_at).label("last_error_at"),
            sqlfunc.count(AIPrediction.id).label("error_count"),
        )
        .where(AIPrediction.error_code.isnot(None))
        .group_by(AIPrediction.model_version)
        .subquery()
    )

    # Get latest error message per model_version
    latest_errors = {}
    error_rows = list(session.execute(
        select(AIPrediction.model_version, AIPrediction.error_message, AIPrediction.created_at)
        .where(AIPrediction.error_code.isnot(None))
        .order_by(AIPrediction.model_version, AIPrediction.created_at.desc())
    ))
    for row in error_rows:
        if row[0] not in latest_errors:
            latest_errors[row[0]] = row[1]

    # Build success/error lookup
    success_lookup = {}
    for row in session.execute(select(success_subq)):
        success_lookup[row[0]] = {"last_success_at": row[1], "success_count": row[2]}

    error_lookup = {}
    for row in session.execute(select(error_subq)):
        error_lookup[row[0]] = {"last_error_at": row[1], "error_count": row[2]}

    result = []
    for model in models:
        provider_config = get_provider_config(model.provider_name)
        has_key = False
        if provider_config:
            from app.config import settings as _settings
            attr = provider_config.api_key_env.lower()
            key = getattr(_settings, attr, "")
            has_key = len(key) > 0

        success_info = success_lookup.get(model.model_version, {})
        error_info = error_lookup.get(model.model_version, {})

        last_success_at = success_info.get("last_success_at")
        last_error_at = error_info.get("last_error_at")
        success_count = success_info.get("success_count", 0)
        error_count = error_info.get("error_count", 0)
        last_error_msg = latest_errors.get(model.model_version)

        # Determine status
        if not is_ai_enabled():
            status = "disabled"
        elif not has_key:
            status = "disabled_no_key"
        elif not model.enabled:
            status = "disabled_by_config"
        elif last_error_at and (not last_success_at or last_error_at > last_success_at):
            status = "provider_error"
        elif model.enabled:
            status = "ready"
        else:
            status = "disabled"

        # Provider health
        provider_available = False
        if has_key:
            if last_success_at and (not last_error_at or last_success_at > last_error_at):
                provider_available = True
            elif last_error_at and (not last_success_at or last_error_at > last_success_at):
                provider_available = False
            elif not last_success_at and not last_error_at:
                provider_available = True  # has key but untested
        provider_health = {"available": provider_available}

        result.append({
            "provider": model.provider_name,
            "model_id": model.model_id,
            "model_version": model.model_version,
            "display_name": model.display_name,
            "enabled": model.enabled and is_ai_enabled(),
            "has_api_key": has_key,
            "cost_tier": model.cost_tier,
            "latency_tier": model.latency_tier,
            "role": model.role,
            "ensemble_weight": model.ensemble_weight,
            "prompt_version": model.prompt_version,
            "include_in_ensemble": model.include_in_ensemble,
            "status": status,
            "disabled_no_key": not has_key,
            "provider_health": provider_health,
            "last_success_at": last_success_at.isoformat() if last_success_at else None,
            "last_error_at": last_error_at.isoformat() if last_error_at else None,
            "last_error": last_error_msg,
            "success_count": success_count,
            "error_count": error_count,
        })

    return result


def _build_prediction_request(session: Session, match_id: str) -> AIPredictionRequest | None:
    """Build a prediction request from database data."""
    match = session.get(Match, match_id)
    if not match:
        return None

    from app.models import Team, TeamRating

    # Get team info
    home_team = session.get(Team, match.home_team_id)
    away_team = session.get(Team, match.away_team_id)
    if not home_team or not away_team:
        return None

    # Get system prediction
    snap = session.scalar(
        select(PredictionSnapshot)
        .where(PredictionSnapshot.match_id == match_id)
        .order_by(PredictionSnapshot.snapshotted_at.desc())
        .limit(1)
    )

    if not snap:
        return None  # No system prediction — refuse to call AI with fake defaults

    system_home_win = snap.home_win
    system_draw = snap.draw
    system_away_win = snap.away_win
    system_home_xg = snap.home_xg
    system_away_xg = snap.away_xg
    model_confidence = snap.confidence
    data_confidence = getattr(snap, "data_confidence", None)

    # Get scorelines
    most_likely = "unknown"
    if snap and snap.scorelines:
        top = snap.scorelines[0]
        most_likely = f"{top.get('home_goals', 1)}-{top.get('away_goals', 0)}"

    # Get market data
    market_snap = session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.match_id == match_id)
        .where(MarketSnapshot.provider == "sporttery")
        .order_by(MarketSnapshot.fetched_at.desc())
        .limit(1)
    )

    # Get intelligence data
    from app.models import MatchIntelligence
    intel_list = list(session.scalars(
        select(MatchIntelligence)
        .where(MatchIntelligence.match_id == match_id)
        .order_by(MatchIntelligence.fetched_at.desc())
    ))

    injuries = []
    suspensions = []
    risk_flags = []
    for intel in intel_list:
        payload = intel.normalized_payload or intel.raw_payload or {}
        if intel.intelligence_type == "injury":
            injuries.append({"player": intel.affected_player_name, "team": intel.affected_team_id, **payload})
        elif intel.intelligence_type == "suspension":
            suspensions.append({"player": intel.affected_player_name, "team": intel.affected_team_id, **payload})
        risk_flags.append(f"{intel.intelligence_type}:{intel.affected_player_name or 'team'}")

    # Get standing context for group matches
    group_context = None
    if match.stage == "group" or (match.group_code and match.group_code in list("ABCDEFGHIJKL")):
        from app.tournament.standings import get_group_context_for_match
        group_context = get_group_context_for_match(session, match)

    # Build knockout context
    knockout_context = None
    if match.stage and match.stage != "group":
        knockout_context = f"Knockout stage: {match.stage}. Single elimination. Draw is possible in 90 minutes, but winner advances (extra time + penalties if needed)."

    # Historical score summary
    from app.services.scoring import model_score_by_version
    try:
        versions = model_score_by_version(session)
        if versions:
            summary_parts = []
            for v in versions[:5]:
                summary_parts.append(
                    f"{v['model_version']}: Brier={v['brier']:.4f}, LogLoss={v['logloss']:.4f}, "
                    f"HitRate={v['hit_rate']:.1%}, n={v['sample_count']}"
                )
            historical_summary = "\n".join(summary_parts)
        else:
            historical_summary = "Insufficient sample - no completed matches scored yet."
    except Exception:
        historical_summary = "Insufficient sample."

    # Market divergence
    market_divergence = None
    if market_snap and system_home_win:
        max_diff = max(
            abs(system_home_win - market_snap.home_probability),
            abs(system_draw - market_snap.draw_probability),
            abs(system_away_win - market_snap.away_probability),
        )
        market_divergence = max_diff

    from app.team_profiles.service import explain_team_profile, get_team_profile, profile_payload
    home_profile = get_team_profile(session, match.home_team_id, match.kickoff)
    away_profile = get_team_profile(session, match.away_team_id, match.kickoff)

    def compact_profile(profile):
        if profile is None:
            return None
        source_summary = profile.source_summary_json or {}
        source_mode = source_summary.get("mode", "unknown")
        is_mock = source_mode == "seed_mock_v1"
        return {
            "traits": profile.traits_json,
            "sample_count": profile.sample_count,
            "draw_resilience_score": profile.draw_resilience_score,
            "favorite_overconfidence_risk": profile.favorite_overconfidence_risk,
            "low_score_tendency": profile.low_score_tendency,
            "summary": explain_team_profile(profile),
            "profile_version": profile.profile_version,
            "profile_as_of": profile.profile_as_of.isoformat(),
            "source_mode": source_mode,
            "sources": source_summary.get("sources", []),
            "is_mock": is_mock,
            "usage_warning": "功能验证数据，不代表真实历史统计；只能作为实验性弱信号，不得作为主要概率调整依据" if is_mock else None,
        }

    return AIPredictionRequest(
        match_id=match_id,
        stage=getattr(match, 'stage', 'group') or 'group',
        group=match.group_code,
        knockout_round=getattr(match, 'round_name', None),
        home_team=home_team.short_name,
        away_team=away_team.short_name,
        kickoff=match.kickoff.isoformat() if match.kickoff else "",
        venue=match.venue,
        neutral_ground=True,  # World Cup matches are neutral ground
        system_home_win=system_home_win,
        system_draw=system_draw,
        system_away_win=system_away_win,
        system_home_xg=system_home_xg,
        system_away_xg=system_away_xg,
        system_model_confidence=model_confidence,
        system_data_confidence=data_confidence or 0.5,
        most_likely_score=most_likely,
        market_home_prob=market_snap.home_probability if market_snap else None,
        market_draw_prob=market_snap.draw_probability if market_snap else None,
        market_away_prob=market_snap.away_probability if market_snap else None,
        market_divergence=market_divergence,
        market_provider=market_snap.provider if market_snap else None,
        market_fetched_at=market_snap.fetched_at.isoformat() if market_snap else None,
        injuries=injuries,
        suspensions=suspensions,
        risk_flags=risk_flags,
        group_standing_context=group_context,
        knockout_context=knockout_context,
        historical_score_summary=historical_summary,
        home_team_profile=compact_profile(home_profile),
        away_team_profile=compact_profile(away_profile),
    )


def _get_provider(provider_config):
    """Get the provider implementation."""
    from app.ai.provider_registry import get_provider
    return get_provider(provider_config.provider_name)


def _get_system_prediction_direction(home_win: float, draw: float, away_win: float) -> str:
    """Get the predicted direction from probabilities."""
    probs = {"home_win": home_win, "draw": draw, "away_win": away_win}
    return max(probs, key=probs.get)


def _safe_json_parse(text: str) -> dict | None:
    """Safely parse JSON, returning None on failure."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _get_baseline_probs(session: Session, match_id: str) -> dict[str, float] | None:
    """Get the baseline system prediction probabilities for a match."""
    from app.models import DashboardRevision, MatchPrediction
    from app.prediction.shadow import SHADOW_MODEL_VERSIONS

    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )
    if not revision:
        return None

    pred = session.scalar(
        select(MatchPrediction)
        .where(MatchPrediction.revision_id == revision.id)
        .where(MatchPrediction.match_id == match_id)
        .where(MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS))
    )
    if not pred:
        return None

    # Use base_home_win if available (unadjusted baseline), otherwise home_win
    home_win = pred.base_home_win if pred.base_home_win is not None else pred.home_win
    draw = pred.base_draw if pred.base_draw is not None else pred.draw
    away_win = pred.base_away_win if pred.base_away_win is not None else pred.away_win

    return {"home_win": home_win, "draw": draw, "away_win": away_win}


def _serialize_ai_prediction(row: AIPrediction, baseline_probs: dict[str, float] | None = None) -> dict[str, Any]:
    """Serialize an AIPrediction row for API output."""
    result = {
        "id": row.id,
        "match_id": row.match_id,
        "provider": row.provider,
        "model_id": row.model_id,
        "model_version": row.model_version,
        "prompt_version": row.prompt_version,
        "parsed_home_win": row.parsed_home_win,
        "parsed_draw": row.parsed_draw,
        "parsed_away_win": row.parsed_away_win,
        "confidence": row.confidence,
        "risk_flags": row.risk_flags_json or [],
        "key_factors": row.key_factors_json or [],
        "reason": row.reason or "",
        "uncertainties": row.uncertainties_json or [],
        "disagreement_with_system": row.disagreement_with_system or "",
        "disagreement_with_market": row.disagreement_with_market or "",
        "recommended_label": row.recommended_label or "uncertain",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "locked_at": row.locked_at.isoformat() if row.locked_at else None,
        "is_pre_match_locked": row.is_pre_match_locked,
        "is_fallback_locked": row.is_fallback_locked,
        "real_time_only": row.real_time_only,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "latency_ms": row.latency_ms,
    }

    # Add baseline comparison if baseline probs are provided
    if baseline_probs and row.parsed_home_win is not None and row.parsed_draw is not None and row.parsed_away_win is not None:
        max_deviation = max(
            abs(row.parsed_home_win - baseline_probs.get("home_win", 0)),
            abs(row.parsed_draw - baseline_probs.get("draw", 0)),
            abs(row.parsed_away_win - baseline_probs.get("away_win", 0)),
        )
        result["identical_to_baseline"] = max_deviation < 0.01
        result["deviation_from_baseline"] = round(max_deviation, 4)
        result["baseline_home_win"] = baseline_probs.get("home_win")
        result["baseline_draw"] = baseline_probs.get("draw")
        result["baseline_away_win"] = baseline_probs.get("away_win")
    else:
        result["identical_to_baseline"] = None
        result["deviation_from_baseline"] = None
        result["baseline_home_win"] = None
        result["baseline_draw"] = None
        result["baseline_away_win"] = None

    return result
