from fastapi import APIRouter
from sqlalchemy import select

from app.db import session_scope
from app.models import Match, PredictionSnapshot
from app.services.scoring import model_score_payload, model_score_by_version


router = APIRouter()


@router.get("/model-score")
def model_score():
    with session_scope() as session:
        payload = model_score_payload(session)
        payload["scoring_snapshot_rule"] = "latest_pre_match_snapshot_before_kickoff"
        return payload


@router.get("/model-score/details")
def model_score_details():
    """Per-match scoring details for all scored matches."""
    with session_scope() as session:
        from app.services.scoring import model_score_details as _details
        from app.services.scoring import get_scoring_exclusions

        details = _details(session)
        exclusions = get_scoring_exclusions(session)

        return {"details": details, "exclusions": exclusions}


@router.get("/model-score/by-version")
def model_score_by_version_endpoint():
    """Aggregate model scores by version with error statistics."""
    with session_scope() as session:
        return {"versions": model_score_by_version(session)}


@router.get("/model-score/by-stage")
def model_score_by_stage():
    """Model scores aggregated by tournament stage."""
    with session_scope() as session:
        from app.services.scoring import model_score_by_stage as _by_stage
        return {"by_stage": _by_stage(session)}


@router.get("/scoring-exclusions")
def scoring_exclusions():
    """List finished matches that were NOT scored and explain why."""
    with session_scope() as session:
        from app.services.scoring import get_scoring_exclusions
        return {"scoring_exclusions": get_scoring_exclusions(session)}


@router.get("/match-count-breakdown")
def match_count_breakdown():
    """Detailed breakdown of match count categories for scoring."""
    with session_scope() as session:
        from app.services.scoring import get_match_count_breakdown
        breakdown = get_match_count_breakdown(session)
        return {
            "total_finished": breakdown.total_finished,
            "has_pre_match_prediction": breakdown.has_pre_match_prediction,
            "has_pre_kickoff_snapshot": breakdown.has_pre_kickoff_snapshot,
            "has_locked_snapshot": breakdown.has_locked_snapshot,
            "has_fallback_snapshot": breakdown.has_fallback_snapshot,
            "actually_scored": breakdown.actually_scored,
            "missing_snapshot": breakdown.missing_snapshot,
            "details": breakdown.details,
            "scoring_snapshot_rule": "latest_pre_match_snapshot_before_kickoff",
        }


@router.get("/error-attribution-summary")
def error_attribution_summary():
    """Aggregate error attribution counts across all scored matches."""
    with session_scope() as session:
        from app.services.scoring import aggregate_error_attributions
        return aggregate_error_attributions(session)


@router.get("/model-calibration")
def model_calibration():
    """Probability calibration analysis."""
    with session_scope() as session:
        from app.services.calibration import compute_calibration
        return {"buckets": compute_calibration(session)}


@router.get("/market-comparison")
def market_comparison():
    """Compare model vs market vs blended predictions."""
    with session_scope() as session:
        from app.services.market_comparison import compute_market_comparison
        return compute_market_comparison(session)


@router.get("/model-recommendation")
def model_recommendation():
    """Recommend which model version to use next."""
    with session_scope() as session:
        from app.services.model_recommendation import get_model_recommendation
        return get_model_recommendation(session)


@router.get("/data-quality")
def data_quality():
    """Data quality check results."""
    with session_scope() as session:
        from app.services.data_quality import check_data_quality
        return check_data_quality(session)


@router.get("/model-comparison")
def model_comparison():
    """Get structured comparison: Baseline vs AI v1 vs AI v2 vs Ensemble."""
    with session_scope() as session:
        from app.services.accuracy_command import get_accuracy_command_center
        data = get_accuracy_command_center(session)
        return {
            "comparison": data.get("model_comparison", []),
            "sample_sufficient": data.get("sample_sufficient", False),
            "sample_count": data.get("sample_count", 0),
        }


@router.get("/model-configs")
def model_configs():
    """List available model configurations."""
    from app.model_configs.model_config_loader import list_configs
    return {"configs": list_configs()}


@router.get("/decision-snapshot-status")
def decision_snapshot_status():
    """Return decision snapshot status for all upcoming and recent matches."""
    from datetime import datetime, timezone, timedelta

    from app.ai.lock_status import compute_decision_snapshot_status

    with session_scope() as session:
        now = datetime.now(timezone.utc)

        # Get upcoming matches (next 7 days) and recent finished matches (last 3 days)
        cutoff_future = now + timedelta(days=7)
        cutoff_past = now - timedelta(days=3)

        matches = list(session.scalars(
            select(Match)
            .where(Match.kickoff >= cutoff_past)
            .where(Match.kickoff <= cutoff_future)
            .order_by(Match.kickoff)
        ))

        result = []
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

            result.append({
                "match_id": match.id,
                "kickoff": match.kickoff.isoformat() if match.kickoff else None,
                "has_decision_snapshot": status.has_decision_snapshot,
                "snapshot_at": status.snapshot_at.isoformat() if status.snapshot_at else None,
                "hours_before_kickoff": status.hours_before_kickoff,
                "is_real_time_only": status.is_real_time_only,
                "participates_in_scoring": status.participates_in_scoring,
                "rule": status.rule,
            })

        overall_status = "ready" if missing == 0 and matches_total > 0 else ("partial" if snapshots_ready > 0 else "none")

        return {
            "decision_snapshot_status": {
                "status": overall_status,
                "matches_total": matches_total,
                "snapshots_ready": snapshots_ready,
                "missing": missing,
                "last_snapshot_at": last_snapshot_at.isoformat() if last_snapshot_at else None,
                "rule": "latest_pre_match_snapshot_before_kickoff",
            },
            "matches": result,
        }


@router.get("/adaptive-weights")
def adaptive_weights():
    """Current adaptive ensemble weights and performance data."""
    with session_scope() as session:
        from app.services.adaptive_weights import compute_adaptive_weights
        return compute_adaptive_weights(session)
