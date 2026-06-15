"""Rolling-origin time-series cross-validation for backtesting."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Ensure datetime is UTC-aware. SQLite returns naive datetimes."""
    if dt is None:
        return dt
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

# Rolling folds - strict available_at boundaries
ROLLING_FOLDS = [
    {
        "name": "fold_1",
        "train_end": datetime(2021, 1, 1, tzinfo=timezone.utc),
        "val_end": datetime(2022, 1, 1, tzinfo=timezone.utc),
        "eval_end": datetime(2023, 1, 1, tzinfo=timezone.utc),
    },
    {
        "name": "fold_2",
        "train_end": datetime(2022, 1, 1, tzinfo=timezone.utc),
        "val_end": datetime(2023, 1, 1, tzinfo=timezone.utc),
        "eval_end": datetime(2024, 1, 1, tzinfo=timezone.utc),
    },
    {
        "name": "fold_3",
        "train_end": datetime(2023, 1, 1, tzinfo=timezone.utc),
        "val_end": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "eval_end": datetime(2025, 1, 1, tzinfo=timezone.utc),
    },
    {
        "name": "fold_4",
        "train_end": datetime(2024, 1, 1, tzinfo=timezone.utc),
        "val_end": datetime(2025, 1, 1, tzinfo=timezone.utc),
        "eval_end": datetime(2026, 6, 11, tzinfo=timezone.utc),
    },
]

AUDIT_TEST_SEEN = {
    "name": "audit_test_seen",
    "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
    "end": datetime(2026, 6, 11, tzinfo=timezone.utc),
    "label": "audit_test_seen (previously viewed, not untouched)",
}


@dataclass
class FoldResult:
    """Results for a single rolling fold."""
    fold_name: str
    train_count: int = 0
    val_count: int = 0
    eval_count: int = 0
    model_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    calibrated_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    draw_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    bootstrap_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    match_ids: list[str] = field(default_factory=list)
    match_id_hash: str = ""


@dataclass
class RollingResult:
    """Results from rolling-origin cross-validation."""
    folds: list[FoldResult] = field(default_factory=list)
    cross_fold_summary: dict[str, dict[str, float]] = field(default_factory=dict)
    oof_bootstrap: dict[str, dict[str, Any]] = field(default_factory=dict)
    admission_decisions: dict[str, str] = field(default_factory=dict)
    data_version: str = ""
    dataset_hash: str = ""


def run_rolling_backtest(session) -> RollingResult:
    """Run rolling-origin time-series cross-validation."""
    from app.models import HistoricalMatch, BacktestResultRecord
    from app.backtesting.elo_replay import replay_elo_history
    from app.backtesting.evaluation import compute_metrics, compute_draw_metrics, MatchPrediction
    from app.backtesting.models import (
        LegacyModel, RefittedModel, DixonColesModel, NegBinomialModel, LogisticModel,
    )
    from app.backtesting.calibration import CALIBRATOR_REGISTRY
    from app.backtesting.bootstrap import paired_bootstrap, brier_sum_fn, log_loss_fn, top1_accuracy_fn

    # Load all valid matches
    all_matches = list(session.scalars(
        select(HistoricalMatch)
        .where(
            HistoricalMatch.is_unmapped.is_(False),
            HistoricalMatch.score_scope == "full_90min",
        )
        .order_by(HistoricalMatch.available_at)
    ))

    # Normalize SQLite naive datetimes to UTC-aware
    for m in all_matches:
        if m.available_at and m.available_at.tzinfo is None:
            m.available_at = m.available_at.replace(tzinfo=timezone.utc)

    # Compute dataset hash
    all_ids = sorted(m.source_match_id for m in all_matches)
    dataset_hash = hashlib.md5(",".join(all_ids).encode()).hexdigest()[:12]

    data_version = "international-history-v1"
    wc_cutoff = datetime(2026, 6, 11, tzinfo=timezone.utc)

    # Collect OOF predictions across all folds
    oof_predictions: dict[str, list[MatchPrediction]] = {}

    folds_result: list[FoldResult] = []

    for fold_def in ROLLING_FOLDS:
        train_matches = [m for m in all_matches if m.available_at < fold_def["train_end"]]
        val_matches = [m for m in all_matches if fold_def["train_end"] <= m.available_at < fold_def["val_end"]]
        eval_matches = [m for m in all_matches if fold_def["val_end"] <= m.available_at < fold_def["eval_end"]]
        eval_matches = [m for m in eval_matches if m.available_at < wc_cutoff]

        fold_result = FoldResult(
            fold_name=fold_def["name"],
            train_count=len(train_matches),
            val_count=len(val_matches),
            eval_count=len(eval_matches),
            match_ids=[m.source_match_id for m in eval_matches],
        )
        fold_result.match_id_hash = hashlib.md5(",".join(sorted(fold_result.match_ids)).encode()).hexdigest()[:12]

        if not eval_matches:
            folds_result.append(fold_result)
            continue

        # Replay Elo on train+val+eval with warmup
        replay = replay_elo_history(
            train_matches + val_matches + eval_matches,
            warmup_cutoff=fold_def["val_end"],
        )
        eval_steps = replay.steps

        # Training-period Elo for normalization (no leakage)
        train_replay = replay_elo_history(train_matches)
        train_steps = train_replay.steps
        train_elo_values = set()
        for step in train_steps:
            train_elo_values.add(step.pre_match_home_elo)
            train_elo_values.add(step.pre_match_away_elo)
        train_elos = sorted(train_elo_values) if train_elo_values else [1500.0]

        # Validation steps for calibrator fitting
        val_replay = replay_elo_history(train_matches + val_matches)
        val_steps = [s for s in val_replay.steps if s.available_at >= fold_def["train_end"]]

        # Initialize and fit models
        models = {
            "legacy-elo-poisson": LegacyModel(),
            "refitted-elo-poisson": RefittedModel(),
            "dixon-coles": DixonColesModel(),
            "neg-binomial": NegBinomialModel(),
            "multinomial-logistic": LogisticModel(),
        }

        if train_steps:
            models["refitted-elo-poisson"].fit(train_steps)
            models["dixon-coles"].fit(train_steps, val_steps=val_steps if val_steps else None)
            models["neg-binomial"].fit(train_steps)
            models["multinomial-logistic"].fit(train_steps)

        # Generate predictions for eval period
        model_preds: dict[str, list[MatchPrediction]] = {}
        for model_name, model in models.items():
            preds = []
            for step in eval_steps:
                try:
                    hw, dr, aw = model.predict(step, train_elos)
                except Exception:
                    hw, dr, aw = 0.4, 0.2, 0.4
                preds.append(MatchPrediction(
                    source_match_id=step.source_match_id,
                    available_at=step.available_at,
                    home_team_id=step.home_team_id,
                    away_team_id=step.away_team_id,
                    home_score=step.home_score,
                    away_score=step.away_score,
                    predicted_home_win=hw, predicted_draw=dr, predicted_away_win=aw,
                    competition_type=step.competition_type,
                    neutral_venue=step.neutral_venue,
                    elo_diff=step.elo_diff,
                    model_name=model_name,
                    data_version=data_version,
                ))
            model_preds[model_name] = preds

            # Uncalibrated metrics
            metrics = compute_metrics(preds, model_name, fold_def["name"], data_version)
            fold_result.model_metrics[model_name] = {
                "eval": {
                    "brier_sum": metrics.brier_sum,
                    "brier_mean": metrics.brier_mean,
                    "canonical_brier": metrics.canonical_brier,
                    "log_loss": metrics.log_loss,
                    "ece": metrics.ece,
                    "top1_hit_rate": metrics.top1_hit_rate,
                    "draw_recall": metrics.draw_recall,
                    "match_count": metrics.match_count,
                }
            }

            # Draw metrics
            dm = compute_draw_metrics(preds)
            fold_result.draw_metrics[model_name] = {
                "draw_brier": dm.draw_brier,
                "draw_log_loss": dm.draw_log_loss,
                "draw_ece": dm.draw_ece,
                "draw_roc_auc": dm.draw_roc_auc,
                "draw_pr_auc": dm.draw_pr_auc,
                "avg_p_draw_when_draw": dm.avg_draw_prob_when_draw,
                "avg_p_draw_when_not_draw": dm.avg_draw_prob_when_not_draw,
                "top1_draw_recall": dm.top1_draw_recall,
                "n_draws": dm.n_draws,
                "n_total": dm.n_total,
            }

            # Collect OOF predictions
            if model_name not in oof_predictions:
                oof_predictions[model_name] = []
            oof_predictions[model_name].extend(preds)

        # Calibrators: fit on validation, evaluate on eval
        val_preds_by_model: dict[str, list[MatchPrediction]] = {}
        if val_steps:
            for model_name, model in models.items():
                val_model_preds = []
                for step in val_steps:
                    try:
                        hw, dr, aw = model.predict(step, train_elos)
                    except Exception:
                        hw, dr, aw = 0.4, 0.2, 0.4
                    val_model_preds.append(MatchPrediction(
                        source_match_id=step.source_match_id,
                        available_at=step.available_at,
                        home_team_id=step.home_team_id,
                        away_team_id=step.away_team_id,
                        home_score=step.home_score,
                        away_score=step.away_score,
                        predicted_home_win=hw, predicted_draw=dr, predicted_away_win=aw,
                        competition_type=step.competition_type,
                        neutral_venue=step.neutral_venue,
                        elo_diff=step.elo_diff,
                        model_name=model_name,
                        data_version=data_version,
                    ))
                val_preds_by_model[model_name] = val_model_preds

        for model_name in models:
            eval_preds = model_preds.get(model_name, [])
            val_preds = val_preds_by_model.get(model_name, [])
            if not eval_preds or not val_preds:
                continue

            for cal_name, CalibratorClass in CALIBRATOR_REGISTRY.items():
                cal = CalibratorClass()
                cal.fit(val_preds)

                cal_preds = []
                for pred in eval_preds:
                    probs = (pred.predicted_home_win, pred.predicted_draw, pred.predicted_away_win)
                    cal_probs = cal.calibrate(probs)
                    cal_preds.append(MatchPrediction(
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
                        model_name=f"{model_name}+{cal_name}",
                        data_version=data_version,
                    ))

                cal_metrics = compute_metrics(cal_preds, f"{model_name}+{cal_name}", fold_def["name"], data_version)
                fold_result.calibrated_metrics[f"{model_name}+{cal_name}"] = {
                    "eval": {
                        "brier_sum": cal_metrics.brier_sum,
                        "brier_mean": cal_metrics.brier_mean,
                        "canonical_brier": cal_metrics.canonical_brier,
                        "log_loss": cal_metrics.log_loss,
                        "ece": cal_metrics.ece,
                        "top1_hit_rate": cal_metrics.top1_hit_rate,
                        "draw_recall": cal_metrics.draw_recall,
                        "match_count": cal_metrics.match_count,
                    }
                }

        # Per-fold bootstrap vs Legacy
        legacy_preds = model_preds.get("legacy-elo-poisson", [])
        for model_name in models:
            if model_name == "legacy-elo-poisson":
                continue
            model_eval_preds = model_preds.get(model_name, [])
            if not legacy_preds or not model_eval_preds:
                continue

            bs_brier = paired_bootstrap(model_eval_preds, legacy_preds, brier_sum_fn, "brier_sum", n_bootstrap=5000, seed=42)
            bs_ll = paired_bootstrap(model_eval_preds, legacy_preds, log_loss_fn, "log_loss", n_bootstrap=5000, seed=42)
            bs_top1 = paired_bootstrap(model_eval_preds, legacy_preds, top1_accuracy_fn, "top1_accuracy", n_bootstrap=5000, seed=42)

            fold_result.bootstrap_results[model_name] = {
                "brier_sum": {
                    "observed_diff": bs_brier.observed_diff,
                    "ci_lower_95": bs_brier.ci_lower_95,
                    "ci_upper_95": bs_brier.ci_upper_95,
                    "p_better": bs_brier.p_better,
                    "conclusion": bs_brier.conclusion,
                    "n_matches": bs_brier.n_matches,
                },
                "log_loss": {
                    "observed_diff": bs_ll.observed_diff,
                    "ci_lower_95": bs_ll.ci_lower_95,
                    "ci_upper_95": bs_ll.ci_upper_95,
                    "p_better": bs_ll.p_better,
                    "conclusion": bs_ll.conclusion,
                },
                "top1_accuracy": {
                    "observed_diff": bs_top1.observed_diff,
                    "ci_lower_95": bs_top1.ci_lower_95,
                    "ci_upper_95": bs_top1.ci_upper_95,
                    "p_better": bs_top1.p_better,
                    "conclusion": bs_top1.conclusion,
                },
            }

        folds_result.append(fold_result)

    # Cross-fold weighted averages
    rolling_result = RollingResult(
        folds=folds_result,
        data_version=data_version,
        dataset_hash=dataset_hash,
    )
    _compute_cross_fold_summary(rolling_result)

    # OOF bootstrap (all evaluation predictions concatenated across folds)
    legacy_oof = oof_predictions.get("legacy-elo-poisson", [])
    for model_name in oof_predictions:
        if model_name == "legacy-elo-poisson":
            continue
        model_oof = oof_predictions[model_name]
        if not legacy_oof or not model_oof:
            continue

        bs = paired_bootstrap(model_oof, legacy_oof, brier_sum_fn, "brier_sum", n_bootstrap=5000, seed=42)
        rolling_result.oof_bootstrap[model_name] = {
            "brier_sum": {
                "observed_diff": bs.observed_diff,
                "ci_lower_95": bs.ci_lower_95,
                "ci_upper_95": bs.ci_upper_95,
                "p_better": bs.p_better,
                "conclusion": bs.conclusion,
                "n_matches": bs.n_matches,
            }
        }

    # Admission decisions based on OOF bootstrap
    _apply_admission(rolling_result)

    # Save to database
    _save_rolling_results(session, rolling_result)

    return rolling_result


def _compute_cross_fold_summary(result: RollingResult) -> None:
    """Compute weighted-average metrics across folds for each model."""
    model_names = set()
    for fold in result.folds:
        model_names.update(fold.model_metrics.keys())

    for model_name in model_names:
        metric_sums: dict[str, float] = {}
        total_weight = 0.0

        for fold in result.folds:
            eval_metrics = fold.model_metrics.get(model_name, {}).get("eval")
            if not eval_metrics:
                continue
            weight = float(eval_metrics.get("match_count", 0))
            if weight == 0:
                continue
            total_weight += weight
            for key in ["brier_sum", "brier_mean", "canonical_brier", "log_loss", "ece", "top1_hit_rate", "draw_recall"]:
                val = eval_metrics.get(key, 0.0)
                metric_sums[key] = metric_sums.get(key, 0.0) + val * weight

        if total_weight > 0:
            result.cross_fold_summary[model_name] = {
                k: v / total_weight for k, v in metric_sums.items()
            }
            result.cross_fold_summary[model_name]["total_matches"] = total_weight


def _apply_admission(result: RollingResult) -> None:
    """Apply admission rules based on OOF bootstrap and cross-fold stability."""
    legacy_summary = result.cross_fold_summary.get("legacy-elo-poisson", {})

    for model_name, summary in result.cross_fold_summary.items():
        if model_name == "legacy-elo-poisson":
            result.admission_decisions[model_name] = "shadow"
            continue

        failures = []
        warnings = []

        # Check Brier not worse
        model_brier = summary.get("brier_sum", 999.0)
        legacy_brier = legacy_summary.get("brier_sum", 0.0)
        if model_brier > legacy_brier + 0.005:
            failures.append(f"Brier worse: {model_brier:.4f} > {legacy_brier:.4f}")
        elif model_brier >= legacy_brier:
            warnings.append(f"Brier not improved: {model_brier:.4f}")

        # Check Log Loss not worse
        model_ll = summary.get("log_loss", 999.0)
        legacy_ll = legacy_summary.get("log_loss", 0.0)
        if model_ll > legacy_ll + 0.01:
            failures.append(f"Log Loss worse: {model_ll:.4f} > {legacy_ll:.4f}")

        # Check ECE not worse
        model_ece = summary.get("ece", 1.0)
        legacy_ece = legacy_summary.get("ece", 0.0)
        if model_ece > legacy_ece + 0.01:
            failures.append(f"ECE worse: {model_ece:.4f} > {legacy_ece:.4f}")

        # Check OOF bootstrap
        oof = result.oof_bootstrap.get(model_name, {}).get("brier_sum", {})
        oof_conclusion = oof.get("conclusion", "inconclusive")
        if "significantly worse" in oof_conclusion:
            failures.append(f"OOF bootstrap: {oof_conclusion}")

        # Check cross-fold stability: model should not be worse in majority of folds
        worse_folds = 0
        total_folds = 0
        for fold in result.folds:
            fold_eval = fold.model_metrics.get(model_name, {}).get("eval")
            fold_legacy = fold.model_metrics.get("legacy-elo-poisson", {}).get("eval")
            if fold_eval and fold_legacy:
                total_folds += 1
                if fold_eval.get("brier_sum", 999) > fold_legacy.get("brier_sum", 0) + 0.005:
                    worse_folds += 1
        if total_folds > 0 and worse_folds > total_folds / 2:
            failures.append(f"Worse in {worse_folds}/{total_folds} folds")

        if failures:
            result.admission_decisions[model_name] = "rejected"
        elif warnings:
            result.admission_decisions[model_name] = "research"
        else:
            result.admission_decisions[model_name] = "shadow"


def _save_rolling_results(session: Any, result: RollingResult) -> None:
    """Save rolling backtest results to database."""
    from app.models import BacktestResultRecord

    for fold in result.folds:
        for model_name, metrics_dict in fold.model_metrics.items():
            eval_m = metrics_dict.get("eval", {})
            admission = result.admission_decisions.get(model_name, "pending")

            record = BacktestResultRecord(
                data_version=result.data_version,
                model_name=model_name,
                split_name=f"rolling_{fold.fold_name}",
                brier_sum=eval_m.get("brier_sum", 0.0),
                brier_mean=eval_m.get("brier_mean", 0.0),
                canonical_brier=eval_m.get("canonical_brier", 0.0),
                log_loss=eval_m.get("log_loss", 0.0),
                ece=eval_m.get("ece", 0.0),
                top1_hit_rate=eval_m.get("top1_hit_rate", 0.0),
                draw_recall=eval_m.get("draw_recall", 0.0),
                match_count=eval_m.get("match_count", 0),
                parameters_json={"fold": fold.fold_name, "match_id_hash": fold.match_id_hash},
                stratified_json=fold.draw_metrics.get(model_name),
                admission_status=admission,
            )
            session.add(record)

    session.flush()
