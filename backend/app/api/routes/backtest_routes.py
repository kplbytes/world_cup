"""API routes for backtesting."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select, func, desc
from sqlalchemy.orm import Session

from app.config import settings
from app.db import session_scope, get_engine
from app.models import BacktestResultRecord

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/backtest", tags=["backtest"])


def _get_session():
    engine = get_engine()
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


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
                "brier_sum": r.brier_sum,
                "brier_mean": r.brier_mean,
                "canonical_brier": r.canonical_brier,
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
                        "brier_sum": m.brier_sum,
                        "brier_mean": m.brier_mean,
                        "canonical_brier": m.canonical_brier,
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


@router.get("/rolling")
def get_rolling_results(session: Session = Depends(_get_session)):
    """Get rolling-origin backtest results."""
    records = list(session.scalars(
        select(BacktestResultRecord)
        .where(BacktestResultRecord.split_name.like("fold_%"))
        .order_by(BacktestResultRecord.split_name, BacktestResultRecord.model_name)
    ))
    # Group by fold
    folds = {}
    for r in records:
        fold_name = r.split_name
        if fold_name not in folds:
            folds[fold_name] = {"fold_name": fold_name, "train_count": 0, "val_count": 0, "eval_count": 0, "model_metrics": {}}
        folds[fold_name]["model_metrics"][r.model_name] = {
            "eval": {
                "brier_sum": r.brier_sum,
                "brier_mean": r.brier_mean,
                "log_loss": r.log_loss,
                "ece": r.ece,
                "top1_hit_rate": r.top1_hit_rate,
                "draw_recall": r.draw_recall,
                "match_count": r.match_count,
            }
        }

    # Compute cross-fold summary
    cross_fold = {}
    for fold_data in folds.values():
        for model_name, metrics in fold_data["model_metrics"].items():
            if model_name not in cross_fold:
                cross_fold[model_name] = {"brier_sum": 0.0, "log_loss": 0.0, "total": 0.0}
            weight = metrics["eval"].get("match_count", 0)
            if weight > 0:
                cross_fold[model_name]["brier_sum"] += metrics["eval"]["brier_sum"] * weight
                cross_fold[model_name]["log_loss"] += metrics["eval"]["log_loss"] * weight
                cross_fold[model_name]["total"] += weight

    for model_name in cross_fold:
        total = cross_fold[model_name]["total"]
        if total > 0:
            cross_fold[model_name]["brier_sum"] /= total
            cross_fold[model_name]["log_loss"] /= total

    return {"folds": list(folds.values()), "cross_fold_summary": cross_fold}
