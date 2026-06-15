"""API routes for backtesting."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func, desc

from app.config import settings
from app.db import session_scope
from app.models import BacktestResultRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.get("/results")
def get_backtest_results():
    """Get latest backtest results as a flat list of model-split records."""
    with session_scope() as session:
        # Get the latest data_version
        latest_version = session.scalar(
            select(func.max(BacktestResultRecord.data_version))
        )
        if not latest_version:
            return {"data_version": None, "models": [], "created_at": None}

        records = list(session.scalars(
            select(BacktestResultRecord)
            .where(BacktestResultRecord.data_version == latest_version)
            .order_by(BacktestResultRecord.model_name, BacktestResultRecord.split_name)
        ))

        models = [
            {
                "model_name": r.model_name,
                "split_name": r.split_name,
                "brier_score": r.brier_score,
                "brier_score_avg": r.brier_score_avg,
                "log_loss": r.log_loss,
                "ece": r.ece,
                "top1_hit_rate": r.top1_hit_rate,
                "draw_recall": r.draw_recall,
                "match_count": r.match_count,
                "admission_status": r.admission_status,
                "parameters": r.parameters_json,
            }
            for r in records
        ]

        created_at = records[0].created_at.isoformat() if records else None

        return {
            "data_version": latest_version,
            "models": models,
            "created_at": created_at,
        }


@router.post("/run")
def trigger_backtest_run():
    """Trigger a backtest run (dev only).

    Only available in non-production environments.
    """
    if settings.environment == "production":
        raise HTTPException(
            status_code=403,
            detail="Backtest run disabled in production environment",
        )

    try:
        from app.backtesting.runner import run_backtest
        with session_scope() as session:
            result = run_backtest(session)

        return {
            "status": "success",
            "data_version": result.data_version,
            "admission_results": result.admission_results,
            "models": {
                model_name: {
                    split_name: {
                        "brier_score": m.brier_score,
                        "brier_score_avg": m.brier_score_avg,
                        "log_loss": m.log_loss,
                        "ece": m.ece,
                        "top1_hit_rate": m.top1_hit_rate,
                        "draw_recall": m.draw_recall,
                        "match_count": m.match_count,
                    }
                    for split_name, m in splits.items()
                }
                for model_name, splits in result.model_results.items()
            },
        }
    except Exception as e:
        logger.error("Backtest run failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Backtest run failed: {e}")


@router.get("/dataset")
def get_dataset_info():
    """Get dataset info for backtesting."""
    from app.backtesting.dataset import build_dataset

    with session_scope() as session:
        dataset = build_dataset(session)

    return {
        "version": dataset.version,
        "created_at": dataset.created_at.isoformat(),
        "total_matches": dataset.total_matches,
        "excluded_wc_2026": dataset.excluded_wc_2026,
        "splits": {
            "train": {
                "match_count": dataset.train.match_count,
                "team_count": dataset.train.team_count,
                "competition_types": dataset.train.competition_types,
                "start": dataset.train.start.isoformat(),
                "end": dataset.train.end.isoformat(),
            },
            "validation": {
                "match_count": dataset.validation.match_count,
                "team_count": dataset.validation.team_count,
                "competition_types": dataset.validation.competition_types,
                "start": dataset.validation.start.isoformat(),
                "end": dataset.validation.end.isoformat(),
            },
            "test": {
                "match_count": dataset.test.match_count,
                "team_count": dataset.test.team_count,
                "competition_types": dataset.test.competition_types,
                "start": dataset.test.start.isoformat(),
                "end": dataset.test.end.isoformat(),
            },
        },
    }
