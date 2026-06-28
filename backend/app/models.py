from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
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
    group_code: Mapped[str | None] = mapped_column(String(1), index=True)
    home_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True)
    away_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    venue: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(24), index=True, default="scheduled")
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(40))
    source_match_id: Mapped[str | None] = mapped_column(String(120), index=True)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tournament stage fields
    stage: Mapped[str] = mapped_column(String(24), index=True, default="group")
    round_name: Mapped[str | None] = mapped_column(String(40))
    bracket_position: Mapped[int | None] = mapped_column(Integer)
    home_team_source: Mapped[str | None] = mapped_column(String(80))
    away_team_source: Mapped[str | None] = mapped_column(String(80))
    winner_to_match_id: Mapped[str | None] = mapped_column(String(80))
    loser_to_match_id: Mapped[str | None] = mapped_column(String(80))
    is_placeholder_match: Mapped[bool] = mapped_column(Boolean, default=False)
    # Knockout advance tracking
    home_advance: Mapped[bool | None] = mapped_column(Boolean)
    away_advance: Mapped[bool | None] = mapped_column(Boolean)
    went_to_extra_time: Mapped[bool | None] = mapped_column(Boolean)
    went_to_penalties: Mapped[bool | None] = mapped_column(Boolean)
    # Penalty shootout score (only set when went_to_penalties is True).
    # Stored separately from home_score/away_score which always reflect the
    # 90-minute (or 120-minute) result, so the UI can render "2-2（点球 4-3）".
    home_penalty_score: Mapped[int | None] = mapped_column(Integer)
    away_penalty_score: Mapped[int | None] = mapped_column(Integer)


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


class TeamProfileMatchHistory(Base):
    __tablename__ = "team_profile_match_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    match_date: Mapped[date] = mapped_column(Date, index=True)
    competition: Mapped[str] = mapped_column(String(80), index=True)
    stage: Mapped[str] = mapped_column(String(40))
    opponent_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True)
    opponent_name: Mapped[str] = mapped_column(String(120))
    opponent_elo: Mapped[float | None] = mapped_column(Float)
    opponent_tier: Mapped[str] = mapped_column(String(16), index=True)
    is_neutral: Mapped[bool] = mapped_column(Boolean, default=True)
    is_home: Mapped[bool] = mapped_column(Boolean, default=False)
    goals_for: Mapped[int] = mapped_column(Integer)
    goals_against: Mapped[int] = mapped_column(Integer)
    result: Mapped[str] = mapped_column(String(8))
    points: Mapped[int] = mapped_column(Integer)
    is_world_cup: Mapped[bool] = mapped_column(Boolean, default=False)
    is_qualifier: Mapped[bool] = mapped_column(Boolean, default=False)
    is_friendly: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TeamProfile(Base):
    __tablename__ = "team_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    team_code: Mapped[str] = mapped_column(String(3), index=True)
    profile_version: Mapped[str] = mapped_column(String(40), index=True)
    profile_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    data_cutoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_start_year: Mapped[int | None] = mapped_column(Integer)
    data_end_year: Mapped[int | None] = mapped_column(Integer)
    sample_count: Mapped[int] = mapped_column(Integer)
    world_cup_sample_count: Mapped[int] = mapped_column(Integer)
    qualifier_sample_count: Mapped[int] = mapped_column(Integer)
    competitive_sample_count: Mapped[int] = mapped_column(Integer)
    attack_strength_recent: Mapped[float] = mapped_column(Float)
    defense_strength_recent: Mapped[float] = mapped_column(Float)
    goal_for_avg: Mapped[float] = mapped_column(Float)
    goal_against_avg: Mapped[float] = mapped_column(Float)
    clean_sheet_rate: Mapped[float] = mapped_column(Float)
    failed_to_score_rate: Mapped[float] = mapped_column(Float)
    over_2_5_rate: Mapped[float] = mapped_column(Float)
    under_2_5_rate: Mapped[float] = mapped_column(Float)
    both_teams_score_rate: Mapped[float] = mapped_column(Float)
    low_score_tendency: Mapped[float] = mapped_column(Float)
    high_score_tendency: Mapped[float] = mapped_column(Float)
    draw_rate_overall: Mapped[float] = mapped_column(Float)
    draw_rate_vs_elite: Mapped[float] = mapped_column(Float)
    draw_rate_vs_strong: Mapped[float] = mapped_column(Float)
    draw_rate_as_underdog: Mapped[float] = mapped_column(Float)
    draw_resilience_score: Mapped[float] = mapped_column(Float)
    favorite_win_rate: Mapped[float] = mapped_column(Float)
    favorite_fail_to_win_rate: Mapped[float] = mapped_column(Float)
    favorite_overconfidence_risk: Mapped[float] = mapped_column(Float)
    weak_opponent_upset_risk: Mapped[float] = mapped_column(Float)
    underdog_draw_rate: Mapped[float] = mapped_column(Float)
    underdog_win_or_draw_rate: Mapped[float] = mapped_column(Float)
    upset_potential_score: Mapped[float] = mapped_column(Float)
    defensive_resilience_score: Mapped[float] = mapped_column(Float)
    world_cup_experience_score: Mapped[float] = mapped_column(Float)
    knockout_experience_score: Mapped[float] = mapped_column(Float)
    recent_tournament_consistency: Mapped[float] = mapped_column(Float)
    pressure_match_score: Mapped[float] = mapped_column(Float)
    opening_match_slow_start_score: Mapped[float] = mapped_column(Float)
    group_stage_consistency: Mapped[float] = mapped_column(Float)
    third_match_rotation_risk: Mapped[float] = mapped_column(Float)
    must_win_match_performance: Mapped[float] = mapped_column(Float)
    tier_stats_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    traits_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_summary_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    long_term_strength_score: Mapped[float] = mapped_column(Float, default=0.0)
    recent_form_score: Mapped[float] = mapped_column(Float, default=0.0)
    attack_score: Mapped[float] = mapped_column(Float, default=0.0)
    defense_score: Mapped[float] = mapped_column(Float, default=0.0)
    stability_score: Mapped[float] = mapped_column(Float, default=0.0)
    tournament_experience_score: Mapped[float] = mapped_column(Float, default=0.0)
    lineup_integrity_score: Mapped[float | None] = mapped_column(Float)
    injury_risk_score: Mapped[float | None] = mapped_column(Float)
    rest_days: Mapped[int | None] = mapped_column(Integer)
    schedule_fatigue_score: Mapped[float | None] = mapped_column(Float)
    environment_adaptation_score: Mapped[float | None] = mapped_column(Float)
    data_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    tactical_style_tags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    strong_opponent_performance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    middle_opponent_performance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    weak_opponent_performance_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strengths_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    weaknesses_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    missing_fields_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    source_list_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    narrative_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_quality_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    profile_modules_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage_scope: Mapped[str] = mapped_column(String(32), default="display_only")
    prediction_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TeamProfilePrediction(Base):
    __tablename__ = "team_profile_predictions"
    __table_args__ = (
        UniqueConstraint("revision_id", "match_id", name="uq_team_profile_predictions_revision_match"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(48), index=True)
    profile_version: Mapped[str] = mapped_column(String(40))
    profile_as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    base_home_win: Mapped[float] = mapped_column(Float)
    base_draw: Mapped[float] = mapped_column(Float)
    base_away_win: Mapped[float] = mapped_column(Float)
    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    home_xg: Mapped[float] = mapped_column(Float)
    away_xg: Mapped[float] = mapped_column(Float)
    probability_deltas_json: Mapped[dict[str, float]] = mapped_column(JSON)
    xg_deltas_json: Mapped[dict[str, float]] = mapped_column(JSON)
    risk_flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    triggered_traits_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text)
    is_pre_match_locked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_fallback_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    real_time_only: Mapped[bool] = mapped_column(Boolean, default=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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
    has_auto_adjustments: Mapped[bool] = mapped_column(Boolean, default=False)
    base_home_win: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_draw: Mapped[float | None] = mapped_column(Float, nullable=True)
    base_away_win: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    __table_args__ = (
        UniqueConstraint("match_id", "revision_id", "model_version", name="uq_prediction_snapshot_match_revision_version"),
        Index("ix_prediction_snapshots_match_id_snapshotted_at", "match_id", "snapshotted_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    revision_id: Mapped[int] = mapped_column(ForeignKey("dashboard_revisions.id"), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    is_pre_match_locked: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_fallback_locked: Mapped[bool] = mapped_column(Boolean, default=False)

    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    home_xg: Mapped[float] = mapped_column(Float)
    away_xg: Mapped[float] = mapped_column(Float)

    has_auto_adjustments: Mapped[bool] = mapped_column(Boolean, default=False)
    base_home_win: Mapped[float | None] = mapped_column(Float)
    base_draw: Mapped[float | None] = mapped_column(Float)
    base_away_win: Mapped[float | None] = mapped_column(Float)

    scorelines: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    score_matrix: Mapped[list[list[float]]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    confidence_label: Mapped[str] = mapped_column(String(16))
    model_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(40))
    snapshotted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MatchIntelligence(Base):
    __tablename__ = "match_intelligence"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    source_url: Mapped[str] = mapped_column(Text)
    intelligence_type: Mapped[str] = mapped_column(String(32), index=True)
    affected_team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id"), index=True)
    affected_player_name: Mapped[str | None] = mapped_column(String(120))
    raw_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    normalized_payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_confidence: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AutoAdjustment(Base):
    __tablename__ = "auto_adjustments"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    source_intelligence_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    affected_team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    adjustment_type: Mapped[str] = mapped_column(String(32))
    attack_delta: Mapped[float] = mapped_column(Float, default=0.0)
    defense_delta: Mapped[float] = mapped_column(Float, default=0.0)
    draw_delta: Mapped[float] = mapped_column(Float, default=0.0)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
    model_version: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ProviderQuotaState(Base):
    __tablename__ = "provider_quota_state"

    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    daily_limit: Mapped[int] = mapped_column(Integer)
    used_today: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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


class AIPrediction(Base):
    """AI model prediction for a match."""
    __tablename__ = "ai_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    provider: Mapped[str] = mapped_column(String(40), index=True)
    model_id: Mapped[str] = mapped_column(String(80), index=True)
    model_version: Mapped[str] = mapped_column(String(80), index=True)
    prompt_version: Mapped[str] = mapped_column(String(40))
    input_snapshot_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    raw_response_text: Mapped[str | None] = mapped_column(Text)
    raw_response_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    parsed_home_win: Mapped[float | None] = mapped_column(Float)
    parsed_draw: Mapped[float | None] = mapped_column(Float)
    parsed_away_win: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    risk_flags_json: Mapped[list[str] | None] = mapped_column(JSON)
    key_factors_json: Mapped[list[str] | None] = mapped_column(JSON)
    reason: Mapped[str | None] = mapped_column(Text)
    uncertainties_json: Mapped[list[str] | None] = mapped_column(JSON)
    disagreement_with_system: Mapped[str | None] = mapped_column(Text)
    disagreement_with_market: Mapped[str | None] = mapped_column(Text)
    recommended_label: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pre_match_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fallback_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    real_time_only: Mapped[bool] = mapped_column(Boolean, default=False)
    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    token_usage_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class EnsemblePrediction(Base):
    """Ensemble prediction combining system, market, and AI predictions."""
    __tablename__ = "ensemble_predictions"

    id: Mapped[int] = mapped_column(primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.id"), index=True)
    model_version: Mapped[str] = mapped_column(String(40))
    system_model_version: Mapped[str | None] = mapped_column(String(40))
    system_weight: Mapped[float] = mapped_column(Float)
    market_weight: Mapped[float] = mapped_column(Float)
    ai_weights_json: Mapped[dict[str, float] | None] = mapped_column(JSON)
    source_probabilities_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ensemble_home_win: Mapped[float] = mapped_column(Float)
    ensemble_draw: Mapped[float] = mapped_column(Float)
    ensemble_away_win: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_pre_match_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    source_status_json: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_type: Mapped[str] = mapped_column(String(40), index=True)  # daily_open / pre_match / lock / post_match / full
    trigger_source: Mapped[str] = mapped_column(String(40))  # auto_on_open / manual_button / script
    status: Mapped[str] = mapped_column(String(24), index=True, default="running")  # running / success / partial_success / failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    options_json: Mapped[dict | None] = mapped_column(JSON)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    step_name: Mapped[str] = mapped_column(String(60))  # refresh_results / post_match_recompute / etc.
    status: Mapped[str] = mapped_column(String(24), default="pending")  # pending / running / success / skipped / failed
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    summary_json: Mapped[dict | None] = mapped_column(JSON)
    error_message: Mapped[str | None] = mapped_column(Text)
