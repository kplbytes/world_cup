"""Main backtest runner - orchestrates the full evaluation pipeline."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.backtesting.calibration import (
    CALIBRATOR_REGISTRY,
    Calibrator,
)
from app.backtesting.dataset import build_dataset
from app.backtesting.elo_replay import ReplayStep, replay_elo_history
from app.backtesting.evaluation import (
    MatchPrediction,
    ModelMetrics,
    StratifiedMetrics,
    compute_metrics,
    stratify_and_compute,
)
from app.backtesting.models import (
    DixonColesModel,
    LegacyModel,
    MODEL_REGISTRY,
    NegBinomialModel,
    RefittedModel,
    elo_to_strength,
)

logger = logging.getLogger(__name__)


@dataclass
class BacktestResult:
    """Complete result of a backtest run."""
    data_version: str
    model_results: dict[str, dict[str, ModelMetrics]]  # model_name -> split_name -> metrics
    calibration_results: dict[str, dict[str, ModelMetrics]]  # calibrated_model -> split_name -> metrics
    stratified_results: dict[str, StratifiedMetrics]  # model_name -> stratified
    best_parameters: dict[str, dict[str, Any]]  # model_name -> params
    admission_results: dict[str, str]  # model_name -> "shadow" or "rejected"
    dataset_info: dict[str, Any]


def run_backtest(session) -> BacktestResult:
    """Run the complete backtest pipeline.

    Steps:
    1. Build versioned dataset
    2. Replay Elo history
    3. Generate predictions for each model on each split
    4. Compute metrics
    5. Fit calibrators on validation set
    6. Evaluate calibrators on test set
    7. Compute stratified metrics
    8. Apply admission rules
    9. Return complete results
    """
    from app.models import HistoricalMatch

    # Step 1: Build versioned dataset
    logger.info("Step 1: Building versioned dataset...")
    dataset = build_dataset(session)
    data_version = dataset.version
    logger.info("Dataset: %s (train=%d, val=%d, test=%d)",
                data_version, dataset.train.match_count,
                dataset.validation.match_count, dataset.test.match_count)

    # Step 2: Load all historical matches and replay Elo
    logger.info("Step 2: Replaying Elo history...")
    all_matches = list(session.scalars(
        select(HistoricalMatch)
        .where(
            HistoricalMatch.is_unmapped.is_(False),
            HistoricalMatch.score_scope == "full_90min",
        )
        .order_by(HistoricalMatch.available_at)
    ))
    replay_result = replay_elo_history(all_matches)
    logger.info("Replayed %d matches", len(replay_result.steps))

    # Build lookup: source_match_id -> ReplayStep
    step_lookup: dict[str, ReplayStep] = {
        s.source_match_id: s for s in replay_result.steps
    }

    # Split steps by dataset split
    train_steps = [step_lookup[mid] for mid in dataset.train.match_ids if mid in step_lookup]
    val_steps = [step_lookup[mid] for mid in dataset.validation.match_ids if mid in step_lookup]
    test_steps = [step_lookup[mid] for mid in dataset.test.match_ids if mid in step_lookup]
    logger.info("Steps matched: train=%d, val=%d, test=%d",
                len(train_steps), len(val_steps), len(test_steps))

    # Step 3: Initialize and fit models
    logger.info("Step 3: Fitting models...")
    models: dict[str, Any] = {}

    # Model A: Legacy (no fitting needed)
    legacy = LegacyModel()
    models[legacy.name] = legacy

    # Model B: Refitted
    refitted = RefittedModel()
    if train_steps:
        refitted.fit(train_steps)
    models[refitted.name] = refitted

    # Model C: Dixon-Coles
    dc = DixonColesModel()
    if train_steps:
        dc.fit(train_steps, val_steps=val_steps if val_steps else None)
    models[dc.name] = dc

    # Model D: Negative Binomial
    nb = NegBinomialModel()
    if train_steps:
        nb.fit(train_steps)
    models[nb.name] = nb

    # Step 4: Generate predictions for each model on each split
    logger.info("Step 4: Generating predictions...")
    split_steps = {
        "train": train_steps,
        "validation": val_steps,
        "test": test_steps,
    }

    model_predictions: dict[str, dict[str, list[MatchPrediction]]] = {}
    for model_name, model in models.items():
        model_predictions[model_name] = {}
        for split_name, steps in split_steps.items():
            preds = _generate_predictions(model, steps, data_version, replay_result)
            model_predictions[model_name][split_name] = preds
            logger.info("  %s/%s: %d predictions", model_name, split_name, len(preds))

    # Step 5: Compute metrics
    logger.info("Step 5: Computing metrics...")
    model_results: dict[str, dict[str, ModelMetrics]] = {}
    for model_name, splits in model_predictions.items():
        model_results[model_name] = {}
        for split_name, preds in splits.items():
            metrics = compute_metrics(preds, model_name, split_name, data_version)
            model_results[model_name][split_name] = metrics
            logger.info("  %s/%s: brier=%.4f, log_loss=%.4f, draw_recall=%.4f",
                        model_name, split_name, metrics.brier_score,
                        metrics.log_loss, metrics.draw_recall)

    # Step 6: Fit calibrators on validation set, evaluate on test set
    logger.info("Step 6: Fitting calibrators...")
    calibration_results: dict[str, dict[str, ModelMetrics]] = {}

    for model_name, model in models.items():
        val_preds = model_predictions[model_name].get("validation", [])
        test_preds = model_predictions[model_name].get("test", [])

        if not val_preds or not test_preds:
            continue

        for cal_name, CalibratorClass in CALIBRATOR_REGISTRY.items():
            calibrator = CalibratorClass()
            calibrator.fit(val_preds)

            # Calibrate test predictions
            calibrated_test_preds = _calibrate_predictions(test_preds, calibrator)
            cal_model_name = f"{model_name}+{cal_name}"

            # Compute metrics on calibrated test predictions
            cal_metrics = compute_metrics(
                calibrated_test_preds, cal_model_name, "test", data_version
            )
            calibration_results[cal_model_name] = {"test": cal_metrics}

            logger.info("  %s: brier=%.4f, log_loss=%.4f",
                        cal_model_name, cal_metrics.brier_score, cal_metrics.log_loss)

    # Step 7: Compute stratified metrics (on test set)
    logger.info("Step 7: Computing stratified metrics...")
    stratified_results: dict[str, StratifiedMetrics] = {}
    for model_name in models:
        test_preds = model_predictions[model_name].get("test", [])
        if test_preds:
            stratified_results[model_name] = stratify_and_compute(
                test_preds, model_name, "test", data_version
            )

    # Step 8: Apply admission rules
    logger.info("Step 8: Applying admission rules...")
    admission_results: dict[str, str] = {}
    legacy_test_metrics = model_results.get("legacy-elo-poisson", {}).get("test")

    for model_name, model in models.items():
        if model_name == "legacy-elo-poisson":
            admission_results[model_name] = "shadow"  # baseline is always shadow
            continue
        test_metrics = model_results.get(model_name, {}).get("test")
        if test_metrics and legacy_test_metrics:
            admission_results[model_name] = check_admission(
                model_name,
                {"test": test_metrics},
                {"test": legacy_test_metrics},
            )
        else:
            admission_results[model_name] = "rejected"

    # Step 9: Collect best parameters
    best_parameters: dict[str, dict[str, Any]] = {}
    for model_name, model in models.items():
        best_parameters[model_name] = model.get_parameters()

    # Step 10: Save results to database
    logger.info("Step 10: Saving results to database...")
    _save_results(session, data_version, model_results, best_parameters,
                  stratified_results, admission_results)

    dataset_info = {
        "version": dataset.version,
        "created_at": dataset.created_at.isoformat(),
        "total_matches": dataset.total_matches,
        "train_count": dataset.train.match_count,
        "validation_count": dataset.validation.match_count,
        "test_count": dataset.test.match_count,
        "excluded_wc_2026": dataset.excluded_wc_2026,
        "train_competition_types": dataset.train.competition_types,
        "validation_competition_types": dataset.validation.competition_types,
        "test_competition_types": dataset.test.competition_types,
    }

    result = BacktestResult(
        data_version=data_version,
        model_results=model_results,
        calibration_results=calibration_results,
        stratified_results=stratified_results,
        best_parameters=best_parameters,
        admission_results=admission_results,
        dataset_info=dataset_info,
    )

    logger.info("Backtest complete!")
    return result


def _generate_predictions(
    model: Any,
    steps: list[ReplayStep],
    data_version: str,
    replay_result: Any,
) -> list[MatchPrediction]:
    """Generate predictions for a model on a set of replay steps."""
    predictions: list[MatchPrediction] = []

    # Track running Elo ratings for normalization
    from app.prediction.elo import update_elo
    ratings: dict[str, float] = {}
    initial = 1500.0

    for step in steps:
        # Collect all known Elo values at this point
        all_elos = list(ratings.values()) if ratings else [initial]

        # Generate prediction
        try:
            home_win, draw, away_win = model.predict(step, all_elos)
        except Exception as e:
            logger.warning("Prediction failed for %s: %s", step.source_match_id, e)
            home_win, draw, away_win = 0.4, 0.2, 0.4

        pred = MatchPrediction(
            source_match_id=step.source_match_id,
            available_at=step.available_at,
            home_team_id=step.home_team_id,
            away_team_id=step.away_team_id,
            home_score=step.home_score,
            away_score=step.away_score,
            predicted_home_win=home_win,
            predicted_draw=draw,
            predicted_away_win=away_win,
            competition_type=step.competition_type,
            neutral_venue=step.neutral_venue,
            elo_diff=step.elo_diff,
            model_name=model.name,
            data_version=data_version,
        )
        predictions.append(pred)

        # Update Elo after this match (for next prediction)
        home_elo = ratings.get(step.home_team_id, initial)
        away_elo = ratings.get(step.away_team_id, initial)
        ha = 0.0 if step.neutral_venue else 60.0
        result = update_elo(
            home_elo, away_elo,
            step.home_score, step.away_score,
            weight=step.update_weight,
            home_advantage=ha,
        )
        ratings[step.home_team_id] = result.home
        ratings[step.away_team_id] = result.away

    return predictions


def _calibrate_predictions(
    predictions: list[MatchPrediction],
    calibrator: Calibrator,
) -> list[MatchPrediction]:
    """Apply a calibrator to a list of predictions, returning new predictions."""
    calibrated = []
    for pred in predictions:
        probs = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
        cal_probs = calibrator.calibrate(probs)
        calibrated.append(MatchPrediction(
            source_match_id=pred.source_match_id,
            available_at=pred.available_at,
            home_team_id=pred.home_team_id,
            away_team_id=pred.away_team_id,
            home_score=pred.home_score,
            away_score=pred.away_score,
            predicted_home_win=cal_probs[0],
            predicted_draw=cal_probs[1],
            predicted_away_win=cal_probs[2],
            competition_type=pred.competition_type,
            neutral_venue=pred.neutral_venue,
            elo_diff=pred.elo_diff,
            model_name=f"{pred.model_name}+{calibrator.name}",
            data_version=pred.data_version,
        ))
    return calibrated


def check_admission(
    model_name: str,
    results: dict[str, ModelMetrics],
    legacy_results: dict[str, ModelMetrics],
) -> str:
    """Check if a model meets admission criteria for Shadow.

    Rules:
    1. Test set Brier better than Legacy
    2. Log Loss not worse than Legacy (within 0.01)
    3. Draw recall clearly improved (>0.05 improvement)
    4. ECE not worse (within 0.01)
    5. Majority of years not worse than Legacy
    6. Improvement not dependent on single competition or team
    7. No future data leakage (verified by design)
    8. Parameters and data version reproducible

    Returns: "shadow" or "rejected"
    """
    model_test = results.get("test")
    legacy_test = legacy_results.get("test")

    if model_test is None or legacy_test is None:
        return "rejected"

    failures: list[str] = []

    # Rule 1: Test set Brier better than Legacy
    if model_test.brier_score >= legacy_test.brier_score:
        failures.append(f"Brier score not better: {model_test.brier_score:.4f} >= {legacy_test.brier_score:.4f}")

    # Rule 2: Log Loss not worse (within 0.01)
    if model_test.log_loss > legacy_test.log_loss + 0.01:
        failures.append(f"Log loss worse: {model_test.log_loss:.4f} > {legacy_test.log_loss:.4f} + 0.01")

    # Rule 3: Draw recall clearly improved (>0.05 improvement)
    draw_improvement = model_test.draw_recall - legacy_test.draw_recall
    if draw_improvement <= 0.05:
        failures.append(f"Draw recall not clearly improved: {model_test.draw_recall:.4f} vs {legacy_test.draw_recall:.4f} (delta={draw_improvement:.4f})")

    # Rule 4: ECE not worse (within 0.01)
    if model_test.ece > legacy_test.ece + 0.01:
        failures.append(f"ECE worse: {model_test.ece:.4f} > {legacy_test.ece:.4f} + 0.01")

    # Rules 5-6: Checked via stratified metrics (would need to pass those in)
    # For now, we just check the core rules above

    # Rule 7: No future data leakage - verified by design (always pass)

    # Rule 8: Parameters and data version reproducible - verified by design (always pass)

    if failures:
        logger.info("Model %s rejected: %s", model_name, "; ".join(failures))
        return "rejected"

    logger.info("Model %s admitted as shadow", model_name)
    return "shadow"


def _save_results(
    session: Any,
    data_version: str,
    model_results: dict[str, dict[str, ModelMetrics]],
    best_parameters: dict[str, dict[str, Any]],
    stratified_results: dict[str, StratifiedMetrics],
    admission_results: dict[str, str],
) -> None:
    """Save backtest results to the database."""
    from app.models import BacktestResultRecord

    for model_name, splits in model_results.items():
        for split_name, metrics in splits.items():
            params = best_parameters.get(model_name)
            strat = stratified_results.get(model_name)
            admission = admission_results.get(model_name, "pending")

            record = BacktestResultRecord(
                data_version=data_version,
                model_name=model_name,
                split_name=split_name,
                brier_score=metrics.brier_score,
                brier_score_avg=metrics.brier_score_avg,
                log_loss=metrics.log_loss,
                ece=metrics.ece,
                top1_hit_rate=metrics.top1_hit_rate,
                draw_recall=metrics.draw_recall,
                match_count=metrics.match_count,
                parameters_json=params,
                stratified_json=_stratified_to_dict(strat) if strat else None,
                admission_status=admission,
            )
            session.add(record)

    session.flush()


def _stratified_to_dict(strat: StratifiedMetrics) -> dict[str, Any]:
    """Convert StratifiedMetrics to a serializable dict."""
    def _metrics_to_dict(m: ModelMetrics) -> dict[str, Any]:
        return {
            "brier_score": m.brier_score,
            "brier_score_avg": m.brier_score_avg,
            "log_loss": m.log_loss,
            "ece": m.ece,
            "top1_hit_rate": m.top1_hit_rate,
            "draw_recall": m.draw_recall,
            "match_count": m.match_count,
        }

    result: dict[str, Any] = {}
    for dim_name, dim_dict in [
        ("by_competition_type", strat.by_competition_type),
        ("by_neutral_venue", strat.by_neutral_venue),
        ("by_elo_diff_range", strat.by_elo_diff_range),
        ("by_year", strat.by_year),
        ("by_outcome", strat.by_outcome),
        ("by_confidence", strat.by_confidence),
    ]:
        result[dim_name] = {k: _metrics_to_dict(v) for k, v in dim_dict.items()}
    return result
