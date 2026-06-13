from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    code: Mapped[str] = mapped_column(String(3), unique=True, nullable=False)
    group_code: Mapped[str] = mapped_column(String(1), index=True, nullable=False)
    flag_url: Mapped[str | None] = mapped_column(Text)


class TeamAlias(Base):
    __tablename__ = "team_aliases"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    alias: Mapped[str] = mapped_column(String(120), index=True)


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    group_code: Mapped[str] = mapped_column(String(1), index=True)
    home_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), index=True, default="scheduled")
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
    source_match_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TeamRating(Base):
    __tablename__ = "team_ratings"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    effective_date: Mapped[date] = mapped_column(Date, index=True)
    fifa_rank: Mapped[int | None] = mapped_column(Integer)
    fifa_points: Mapped[float | None] = mapped_column(Float)
    elo: Mapped[float] = mapped_column(Float, default=1500.0)
    recent_form: Mapped[str] = mapped_column(String(10), default="")
    source: Mapped[str] = mapped_column(String(80))


class HistoricalMatch(Base):
    __tablename__ = "historical_matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    played_on: Mapped[date] = mapped_column(Date, index=True)
    home_team: Mapped[str] = mapped_column(String(120), index=True)
    away_team: Mapped[str] = mapped_column(String(120), index=True)
    home_score: Mapped[int] = mapped_column(Integer)
    away_score: Mapped[int] = mapped_column(Integer)
    tournament: Mapped[str] = mapped_column(String(160))
    neutral: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(80))


class DashboardRevision(Base):
    __tablename__ = "dashboard_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    model_version: Mapped[str] = mapped_column(String(40))
    simulation_iterations: Mapped[int] = mapped_column(Integer)
    simulation_seed: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, index=True, default=False)


class StandingSnapshot(Base):
    __tablename__ = "standings_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    group_code: Mapped[str] = mapped_column(String(1), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    played: Mapped[int] = mapped_column(Integer)
    won: Mapped[int] = mapped_column(Integer)
    drawn: Mapped[int] = mapped_column(Integer)
    lost: Mapped[int] = mapped_column(Integer)
    goals_for: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer)
    tiebreak_uncertain: Mapped[bool] = mapped_column(Boolean, default=False)


class MatchPrediction(Base):
    __tablename__ = "match_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    home_xg: Mapped[float] = mapped_column(Float)
    away_xg: Mapped[float] = mapped_column(Float)
    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    scorelines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    score_matrix: Mapped[list[list[float]]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_label: Mapped[str] = mapped_column(String(16))
    data_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    data_confidence_label: Mapped[str | None] = mapped_column(String(16))
    model_confidence: Mapped[float] = mapped_column(Float, nullable=True)
    model_confidence_label: Mapped[str | None] = mapped_column(String(16))
    explanation: Mapped[str] = mapped_column(Text)
    model_inputs: Mapped[dict[str, Any]] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(40))


class PredictionSnapshot(Base):
    __tablename__ = "prediction_snapshots"

    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    home_xg: Mapped[float] = mapped_column(Float)
    away_xg: Mapped[float] = mapped_column(Float)
    scorelines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    score_matrix: Mapped[list[list[float]]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_label: Mapped[str] = mapped_column(String(16))
    model_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(40))
    snapshotted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ModelScore(Base):
    __tablename__ = "model_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    matches_scored: Mapped[int] = mapped_column(Integer)
    brier_score: Mapped[float] = mapped_column(Float)
    log_loss: Mapped[float] = mapped_column(Float)
    outcome_hit_rate: Mapped[float] = mapped_column(Float)
    top_score_hit_rate: Mapped[float] = mapped_column(Float)
    xg_mae: Mapped[float] = mapped_column(Float)
    per_match: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ManualAdjustment(Base):
    __tablename__ = "manual_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    adjustment_type: Mapped[str] = mapped_column(String(32))
    affected_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    attack_delta: Mapped[float] = mapped_column(Float, default=0.0)
    defense_delta: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[str] = mapped_column(String(16), default="medium")
    note: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(40), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class QualificationPrediction(Base):
    __tablename__ = "qualification_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    first_probability: Mapped[float] = mapped_column(Float)
    second_probability: Mapped[float] = mapped_column(Float)
    third_probability: Mapped[float] = mapped_column(Float)
    fourth_probability: Mapped[float] = mapped_column(Float)
    qualify_probability: Mapped[float] = mapped_column(Float)
    standard_error: Mapped[float] = mapped_column(Float)


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    status: Mapped[str] = mapped_column(String(24), index=True)
    checksum: Mapped[str | None] = mapped_column(String(64))
    local_path: Mapped[str | None] = mapped_column(Text)
    coverage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), index=True)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    finalized_matches: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    errors: Mapped[list[str]] = mapped_column(JSON, default=list)


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    home_probability: Mapped[float] = mapped_column(Float)
    draw_probability: Mapped[float] = mapped_column(Float)
    away_probability: Mapped[float] = mapped_column(Float)
    raw_overround: Mapped[float] = mapped_column(Float)
    source_match_id: Mapped[str | None] = mapped_column(String(120))
