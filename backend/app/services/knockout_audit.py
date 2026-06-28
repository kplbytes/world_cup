from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AIPrediction, EnsemblePrediction, Match, PredictionSnapshot
from app.services.scoring import (
    _aggregate_snapshot_rows_by_stage,
    _scorable_snapshot_rows,
    _scorable_snapshot_rows_by_version,
    _ensure_utc,
    get_scoring_exclusions,
)


STAGE_ORDER = [
    "round_of_32",
    "round_of_16",
    "quarter_final",
    "semi_final",
    "third_place",
    "final",
]

STAGE_LABELS = {
    "round_of_32": "32强",
    "round_of_16": "16强",
    "quarter_final": "四分之一决赛",
    "semi_final": "半决赛",
    "third_place": "三四名决赛",
    "final": "决赛",
}


def _is_visible_ai_version(model_version: str | None) -> bool:
    if not model_version:
        return False
    lowered = model_version.lower()
    if "mimo" in lowered or "xiaomi" in lowered:
        return False
    return True
def _stage_label(stage: str | None, round_name: str | None = None) -> str:
    if round_name:
        return round_name
    return STAGE_LABELS.get(stage or "", stage or "未知阶段")


def _empty_finished_stage(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "stage_label": _stage_label(stage),
        "total_matches": 0,
        "finished_matches": 0,
        "scored_matches": 0,
        "excluded_matches": 0,
        "versions": [],
    }


def _empty_upcoming_stage(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "stage_label": _stage_label(stage),
        "total_matches": 0,
        "finished_matches": 0,
        "real_upcoming_matches": 0,
        "placeholder_upcoming_matches": 0,
        "within_48h_matches": 0,
        "baseline_ready": 0,
        "ai_ready": 0,
        "ensemble_ready": 0,
        "ai_needed_now": 0,
    }


def get_knockout_audit(session: Session) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    cutoff_48h = now + timedelta(hours=48)

    knockout_matches = list(
        session.scalars(
            select(Match)
            .where(Match.group_code.is_(None))
            .order_by(Match.kickoff, Match.id)
        )
    )

    if not knockout_matches:
        return {
            "summary": {
                "total_matches": 0,
                "finished_matches": 0,
                "scored_matches": 0,
                "real_upcoming_matches": 0,
                "placeholder_upcoming_matches": 0,
                "within_48h_matches": 0,
                "baseline_ready": 0,
                "ai_ready": 0,
                "ensemble_ready": 0,
                "ai_needed_now": 0,
                "can_validate_effectiveness": False,
                "auto_ai_workflow_enabled": settings.ai_run_mode == "auto",
                "scheduled_refresh_enabled": settings.enable_scheduled_refresh,
                "message": "当前没有淘汰赛数据。",
                "critical_gaps": ["no_knockout_matches"],
            },
            "finished_by_stage": [],
            "upcoming_by_stage": [],
            "exclusions": [],
            "exclusion_summary_by_stage": [],
        }

    knockout_match_ids = {match.id for match in knockout_matches}
    real_upcoming = [
        match
        for match in knockout_matches
        if match.status != "final" and match.home_team_id and match.away_team_id
    ]

    match_ids = [match.id for match in real_upcoming]
    baseline_match_ids = (
        set(
            session.scalars(
                select(PredictionSnapshot.match_id)
                .where(PredictionSnapshot.match_id.in_(match_ids))
                .distinct()
            )
        )
        if match_ids
        else set()
    )
    ai_match_ids = (
        {
            row.match_id
            for row in session.scalars(
                select(AIPrediction)
                .where(AIPrediction.match_id.in_(match_ids))
                .where(AIPrediction.error_code.is_(None))
                .where(AIPrediction.parsed_home_win.is_not(None))
                .where(AIPrediction.parsed_draw.is_not(None))
                .where(AIPrediction.parsed_away_win.is_not(None))
            )
            if _is_visible_ai_version(row.model_version)
        }
        if match_ids
        else set()
    )
    ensemble_match_ids = (
        set(
            session.scalars(
                select(EnsemblePrediction.match_id)
                .where(EnsemblePrediction.match_id.in_(match_ids))
                .distinct()
            )
        )
        if match_ids
        else set()
    )

    knockout_scored_rows = [
        (snap, match)
        for snap, match in _scorable_snapshot_rows(session)
        if match.id in knockout_match_ids
    ]
    knockout_scored_match_ids = {match.id for _snap, match in knockout_scored_rows}
    knockout_stage_scores = _aggregate_snapshot_rows_by_stage(
        [(snap, match) for snap, match in _scorable_snapshot_rows_by_version(session) if match.id in knockout_match_ids]
    )
    knockout_exclusions = [
        exclusion
        for exclusion in get_scoring_exclusions(session, scored_rows=_scorable_snapshot_rows(session))
        if exclusion.get("stage") and exclusion.get("stage") != "group"
    ]

    finished_by_stage = {stage: _empty_finished_stage(stage) for stage in STAGE_ORDER}
    upcoming_by_stage = {stage: _empty_upcoming_stage(stage) for stage in STAGE_ORDER}
    exclusion_summary: dict[str, dict[str, Any]] = {
        stage: {
            "stage": stage,
            "stage_label": _stage_label(stage),
            "excluded_matches": 0,
            "reason_counts": {},
        }
        for stage in STAGE_ORDER
    }

    for match in knockout_matches:
        stage = match.stage or "unknown"
        finished_bucket = finished_by_stage.setdefault(stage, _empty_finished_stage(stage))
        upcoming_bucket = upcoming_by_stage.setdefault(stage, _empty_upcoming_stage(stage))
        finished_bucket["total_matches"] += 1
        upcoming_bucket["total_matches"] += 1
        finished_bucket["stage_label"] = _stage_label(stage, match.round_name)
        upcoming_bucket["stage_label"] = _stage_label(stage, match.round_name)

        if match.status == "final":
            finished_bucket["finished_matches"] += 1
            upcoming_bucket["finished_matches"] += 1
            if match.id in knockout_scored_match_ids:
                finished_bucket["scored_matches"] += 1
            continue

        if match.home_team_id and match.away_team_id:
            upcoming_bucket["real_upcoming_matches"] += 1
            kickoff = _ensure_utc(match.kickoff)
            is_within_48h = kickoff is not None and kickoff <= cutoff_48h
            if is_within_48h:
                upcoming_bucket["within_48h_matches"] += 1
            if match.id in baseline_match_ids:
                upcoming_bucket["baseline_ready"] += 1
            if match.id in ai_match_ids:
                upcoming_bucket["ai_ready"] += 1
            elif is_within_48h:
                upcoming_bucket["ai_needed_now"] += 1
            if match.id in ensemble_match_ids:
                upcoming_bucket["ensemble_ready"] += 1
        else:
            upcoming_bucket["placeholder_upcoming_matches"] += 1

    for stage, versions in knockout_stage_scores.items():
        bucket = finished_by_stage.setdefault(stage, _empty_finished_stage(stage))
        bucket["versions"] = versions

    for exclusion in knockout_exclusions:
        stage = exclusion.get("stage") or "unknown"
        finished_by_stage.setdefault(stage, _empty_finished_stage(stage))["excluded_matches"] += 1
        stage_summary = exclusion_summary.setdefault(
            stage,
            {
                "stage": stage,
                "stage_label": _stage_label(stage, exclusion.get("round_name")),
                "excluded_matches": 0,
                "reason_counts": {},
            },
        )
        stage_summary["stage_label"] = _stage_label(stage, exclusion.get("round_name"))
        stage_summary["excluded_matches"] += 1
        for reason_code in exclusion.get("reason_codes", []):
            stage_summary["reason_counts"][reason_code] = stage_summary["reason_counts"].get(reason_code, 0) + 1

    finished_rows = [finished_by_stage[stage] for stage in STAGE_ORDER if stage in finished_by_stage]
    upcoming_rows = [upcoming_by_stage[stage] for stage in STAGE_ORDER if stage in upcoming_by_stage]
    exclusion_rows = [exclusion_summary[stage] for stage in STAGE_ORDER if stage in exclusion_summary and exclusion_summary[stage]["excluded_matches"] > 0]

    finished_matches = sum(row["finished_matches"] for row in finished_rows)
    scored_matches = sum(row["scored_matches"] for row in finished_rows)
    real_upcoming_matches = sum(row["real_upcoming_matches"] for row in upcoming_rows)
    placeholder_upcoming_matches = sum(row["placeholder_upcoming_matches"] for row in upcoming_rows)
    within_48h_matches = sum(row["within_48h_matches"] for row in upcoming_rows)
    baseline_ready = sum(row["baseline_ready"] for row in upcoming_rows)
    ai_ready = sum(row["ai_ready"] for row in upcoming_rows)
    ensemble_ready = sum(row["ensemble_ready"] for row in upcoming_rows)
    ai_needed_now = sum(row["ai_needed_now"] for row in upcoming_rows)

    critical_gaps: list[str] = []
    if finished_matches == 0:
        critical_gaps.append("no_finished_knockout_samples")
    if within_48h_matches > ai_ready:
        critical_gaps.append("ai_coverage_gap_within_48h")
    if real_upcoming_matches > baseline_ready:
        critical_gaps.append("baseline_gap")
    if real_upcoming_matches > ensemble_ready:
        critical_gaps.append("ensemble_gap")

    auto_ai_enabled = settings.ai_run_mode == "auto"
    scheduled_refresh_enabled = settings.enable_scheduled_refresh

    if finished_matches == 0 and ai_needed_now > 0 and not auto_ai_enabled:
        message = f"当前还没有已完赛淘汰赛样本，暂时无法验证淘汰赛命中率；未来 48 小时有 {within_48h_matches} 场真实淘汰赛，仅 {ai_ready} 场已有 AI，且自动 AI workflow 当前关闭。"
    elif finished_matches == 0 and ai_needed_now > 0:
        message = f"当前还没有已完赛淘汰赛样本，暂时无法验证淘汰赛命中率；未来 48 小时有 {within_48h_matches} 场真实淘汰赛，仅 {ai_ready} 场已有 AI。"
    elif finished_matches == 0:
        message = "当前还没有已完赛淘汰赛样本，暂时只能验证链路 readiness，无法判断淘汰赛命中率。"
    elif ai_needed_now > 0:
        message = f"当前已有 {finished_matches} 场已完赛淘汰赛可用于评分；但未来 48 小时仍有 {ai_needed_now} 场真实淘汰赛缺少 AI 预测。"
    else:
        message = "当前淘汰赛链路可同时查看已完赛评分和未来对阵 readiness。"

    return {
        "summary": {
            "total_matches": len(knockout_matches),
            "finished_matches": finished_matches,
            "scored_matches": scored_matches,
            "real_upcoming_matches": real_upcoming_matches,
            "placeholder_upcoming_matches": placeholder_upcoming_matches,
            "within_48h_matches": within_48h_matches,
            "baseline_ready": baseline_ready,
            "ai_ready": ai_ready,
            "ensemble_ready": ensemble_ready,
            "ai_needed_now": ai_needed_now,
            "can_validate_effectiveness": finished_matches > 0 and scored_matches > 0,
            "auto_ai_workflow_enabled": auto_ai_enabled,
            "scheduled_refresh_enabled": scheduled_refresh_enabled,
            "message": message,
            "critical_gaps": critical_gaps,
        },
        "finished_by_stage": finished_rows,
        "upcoming_by_stage": upcoming_rows,
        "exclusions": knockout_exclusions,
        "exclusion_summary_by_stage": exclusion_rows,
    }
