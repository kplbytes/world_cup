"""Data quality checks for historical match data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import HistoricalMatch


@dataclass
class DataQualityReport:
    """Report of data quality issues found in historical match data."""
    total_matches: int = 0
    duplicate_count: int = 0
    future_match_count: int = 0
    score_anomaly_count: int = 0
    same_team_count: int = 0
    unmapped_team_count: int = 0
    neutral_venue_missing_world_cup: int = 0
    time_regression_count: int = 0
    competition_type_counts: dict[str, int] = field(default_factory=dict)
    score_scope_full_90min: int = 0
    score_scope_after_extra_time: int = 0
    score_scope_unknown: int = 0
    issues: list[str] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return (
            self.duplicate_count == 0
            and self.future_match_count == 0
            and self.score_anomaly_count == 0
            and self.same_team_count == 0
            and self.time_regression_count == 0
        )


def run_quality_checks(session: Session) -> DataQualityReport:
    """Run all data quality checks and return a report."""
    report = DataQualityReport()
    now = datetime.now(timezone.utc)

    # Total matches
    report.total_matches = session.scalar(
        select(func.count(HistoricalMatch.id))
    ) or 0

    if report.total_matches == 0:
        report.issues.append("No historical matches found")
        return report

    # Duplicate matches (same provider + source_match_id)
    dup_rows = session.execute(
        select(HistoricalMatch.provider, HistoricalMatch.source_match_id, func.count())
        .group_by(HistoricalMatch.provider, HistoricalMatch.source_match_id)
        .having(func.count() > 1)
    ).fetchall()
    report.duplicate_count = sum(r[2] - 1 for r in dup_rows)
    if report.duplicate_count > 0:
        report.issues.append(f"Found {report.duplicate_count} duplicate provider+source_match_id records")

    # Time anomalies (matches in the future)
    report.future_match_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.kickoff > now)
    ) or 0
    if report.future_match_count > 0:
        report.issues.append(f"Found {report.future_match_count} matches with future dates")

    # Score anomalies (scores > 20)
    report.score_anomaly_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(
            (HistoricalMatch.home_score > 20) | (HistoricalMatch.away_score > 20)
        )
    ) or 0
    if report.score_anomaly_count > 0:
        report.issues.append(f"Found {report.score_anomaly_count} matches with scores > 20")

    # Same home/away team
    report.same_team_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(
            HistoricalMatch.home_team_raw == HistoricalMatch.away_team_raw
        )
    ) or 0
    if report.same_team_count > 0:
        report.issues.append(f"Found {report.same_team_count} matches with same home/away team")

    # Unmapped teams
    report.unmapped_team_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(HistoricalMatch.is_unmapped.is_(True))
    ) or 0
    if report.unmapped_team_count > 0:
        report.issues.append(f"Found {report.unmapped_team_count} matches with unmapped teams")

    # Neutral venue missing for world cup matches
    report.neutral_venue_missing_world_cup = session.scalar(
        select(func.count(HistoricalMatch.id)).where(
            HistoricalMatch.competition_type == "world_cup",
            HistoricalMatch.neutral_venue.is_(False),
        )
    ) or 0
    if report.neutral_venue_missing_world_cup > 0:
        report.issues.append(
            f"Found {report.neutral_venue_missing_world_cup} World Cup matches without neutral venue flag"
        )

    # Data update time regression (fetched_at before source_updated_at)
    report.time_regression_count = session.scalar(
        select(func.count(HistoricalMatch.id)).where(
            HistoricalMatch.source_updated_at.isnot(None),
            HistoricalMatch.fetched_at < HistoricalMatch.source_updated_at,
        )
    ) or 0
    if report.time_regression_count > 0:
        report.issues.append(f"Found {report.time_regression_count} records with time regression")

    # Match count by competition_type
    type_rows = session.execute(
        select(HistoricalMatch.competition_type, func.count())
        .group_by(HistoricalMatch.competition_type)
    ).fetchall()
    report.competition_type_counts = {r[0]: r[1] for r in type_rows}

    # Score scope counts
    scope_rows = session.execute(
        select(HistoricalMatch.score_scope, func.count())
        .group_by(HistoricalMatch.score_scope)
    ).fetchall()
    for scope, cnt in scope_rows:
        if scope == "full_90min":
            report.score_scope_full_90min = cnt
        elif scope == "after_extra_time_or_unknown":
            report.score_scope_after_extra_time = cnt
        elif scope == "unknown_score_scope":
            report.score_scope_unknown = cnt

    return report
