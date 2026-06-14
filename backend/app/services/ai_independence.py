from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AIPrediction, DashboardRevision, MatchPrediction
from app.prediction.shadow import SHADOW_MODEL_VERSIONS


BUCKET_ORDER = ("identical", "slight", "moderate", "strong")
TOP_RECORD_LIMIT = 10


def _bucket_for_delta(max_abs_delta: float) -> str:
    if max_abs_delta < 0.01:
        return "identical"
    if max_abs_delta < 0.03:
        return "slight"
    if max_abs_delta < 0.07:
        return "moderate"
    return "strong"


def _prediction_direction(home: float, draw: float, away: float) -> str:
    probs = {"home_win": home, "draw": draw, "away_win": away}
    return max(probs, key=probs.get)


def _round_metric(value: float) -> float:
    return round(value, 4)


def _baseline_probabilities(row: MatchPrediction) -> dict[str, float]:
    return {
        "home": row.base_home_win if row.base_home_win is not None else row.home_win,
        "draw": row.base_draw if row.base_draw is not None else row.draw,
        "away": row.base_away_win if row.base_away_win is not None else row.away_win,
    }


def _build_bucket_summary(records: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    total = len(records)
    counts = {bucket: 0 for bucket in BUCKET_ORDER}
    for record in records:
        counts[record["bucket"]] += 1
    return {
        bucket: {
            "count": counts[bucket],
            "ratio": _round_metric(counts[bucket] / total) if total else 0.0,
        }
        for bucket in BUCKET_ORDER
    }


def _build_audit_record(row: AIPrediction, baseline: dict[str, float]) -> dict[str, Any]:
    ai_home = float(row.parsed_home_win)
    ai_draw = float(row.parsed_draw)
    ai_away = float(row.parsed_away_win)
    deltas = [
        abs(ai_home - baseline["home"]),
        abs(ai_draw - baseline["draw"]),
        abs(ai_away - baseline["away"]),
    ]
    max_abs_delta = max(deltas)
    mean_abs_delta = sum(deltas) / 3
    direction_same = (
        _prediction_direction(ai_home, ai_draw, ai_away)
        == _prediction_direction(baseline["home"], baseline["draw"], baseline["away"])
    )

    return {
        "match_id": row.match_id,
        "model_version": row.model_version,
        "baseline_home": _round_metric(baseline["home"]),
        "baseline_draw": _round_metric(baseline["draw"]),
        "baseline_away": _round_metric(baseline["away"]),
        "ai_home": _round_metric(ai_home),
        "ai_draw": _round_metric(ai_draw),
        "ai_away": _round_metric(ai_away),
        "max_abs_delta": _round_metric(max_abs_delta),
        "mean_abs_delta": _round_metric(mean_abs_delta),
        "direction_same": direction_same,
        "bucket": _bucket_for_delta(max_abs_delta),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "prompt_version": row.prompt_version,
    }


def analyze_ai_independence(session: Session) -> dict[str, Any]:
    revision = session.scalar(
        select(DashboardRevision)
        .where(DashboardRevision.active.is_(True))
        .order_by(DashboardRevision.id.desc())
        .limit(1)
    )

    valid_ai_predictions = list(
        session.scalars(
            select(AIPrediction)
            .where(AIPrediction.error_code.is_(None))
            .where(AIPrediction.parsed_home_win.is_not(None))
            .where(AIPrediction.parsed_draw.is_not(None))
            .where(AIPrediction.parsed_away_win.is_not(None))
            .order_by(AIPrediction.created_at.desc(), AIPrediction.id.desc())
        )
    )

    if not revision:
        return {
            "active_revision_id": None,
            "summary": {
                "total_valid_ai_prediction_count": len(valid_ai_predictions),
                "audited_prediction_count": 0,
                "missing_baseline_count": len(valid_ai_predictions),
                "buckets": {bucket: {"count": 0, "ratio": 0.0} for bucket in BUCKET_ORDER},
            },
            "by_model_version": {},
            "top_divergent": [],
            "top_aligned": [],
            "records": [],
        }

    baseline_rows = list(
        session.scalars(
            select(MatchPrediction)
            .where(MatchPrediction.revision_id == revision.id)
            .where(MatchPrediction.model_version.notin_(SHADOW_MODEL_VERSIONS))
        )
    )
    baselines_by_match = {row.match_id: _baseline_probabilities(row) for row in baseline_rows}

    records: list[dict[str, Any]] = []
    missing_baseline_count = 0
    grouped_records: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in valid_ai_predictions:
        baseline = baselines_by_match.get(row.match_id)
        if baseline is None:
            missing_baseline_count += 1
            continue

        record = _build_audit_record(row, baseline)
        records.append(record)
        grouped_records[row.model_version].append(record)

    summary = {
        "total_valid_ai_prediction_count": len(valid_ai_predictions),
        "audited_prediction_count": len(records),
        "missing_baseline_count": missing_baseline_count,
        "buckets": _build_bucket_summary(records),
    }

    by_model_version = {}
    for model_version, model_records in sorted(grouped_records.items()):
        count = len(model_records)
        by_model_version[model_version] = {
            "count": count,
            "average_max_abs_delta": _round_metric(sum(item["max_abs_delta"] for item in model_records) / count),
            "average_mean_abs_delta": _round_metric(sum(item["mean_abs_delta"] for item in model_records) / count),
            "direction_same_rate": _round_metric(sum(1 for item in model_records if item["direction_same"]) / count),
            "buckets": _build_bucket_summary(model_records),
        }

    top_divergent = sorted(
        records,
        key=lambda item: (-item["max_abs_delta"], -item["mean_abs_delta"], item["match_id"], item["model_version"]),
    )[:TOP_RECORD_LIMIT]
    top_aligned = sorted(
        records,
        key=lambda item: (item["max_abs_delta"], item["mean_abs_delta"], item["match_id"], item["model_version"]),
    )[:TOP_RECORD_LIMIT]

    return {
        "active_revision_id": revision.id,
        "summary": summary,
        "by_model_version": by_model_version,
        "top_divergent": top_divergent,
        "top_aligned": top_aligned,
        "records": records,
    }
