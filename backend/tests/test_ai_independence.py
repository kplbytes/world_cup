from datetime import datetime, timezone

from app.models import AIPrediction, DashboardRevision, Match, MatchPrediction, Team, TeamRating
from app.services.ai_independence import analyze_ai_independence


def _seed_team(session, team_id: str, group_code: str = "A") -> None:
    session.add(Team(id=team_id, name=team_id, short_name=team_id, code=team_id, group_code=group_code))
    session.add(TeamRating(team_id=team_id, effective_date=datetime(2026, 6, 1, tzinfo=timezone.utc).date(), fifa_rank=1, fifa_points=1000.0, elo=1600.0, recent_form="", source="test"))


def _seed_match(
    session,
    match_id: str = "match-1",
    group_code: str = "A",
    home_team_id: str = "AAA",
    away_team_id: str = "BBB",
) -> Match:
    _seed_team(session, home_team_id, group_code)
    _seed_team(session, away_team_id, group_code)
    session.flush()
    match = Match(
        id=match_id,
        group_code=group_code,
        home_team_id=home_team_id,
        away_team_id=away_team_id,
        kickoff=datetime(2026, 6, 14, 12, tzinfo=timezone.utc),
        venue="Test",
        status="scheduled",
        source="test",
    )
    session.add(match)
    return match


def _seed_active_revision(session) -> DashboardRevision:
    revision = DashboardRevision(
        model_version="elo-poisson-v1",
        simulation_iterations=100,
        simulation_seed=7,
        active=True,
    )
    session.add(revision)
    session.flush()
    return revision


def _seed_baseline(session, revision_id: int, match_id: str, home: float, draw: float, away: float) -> None:
    session.add(
        MatchPrediction(
            revision_id=revision_id,
            match_id=match_id,
            home_xg=1.2,
            away_xg=0.9,
            home_win=home,
            draw=draw,
            away_win=away,
            has_auto_adjustments=False,
            base_home_win=None,
            base_draw=None,
            base_away_win=None,
            scorelines=[],
            score_matrix=[],
            confidence=0.7,
            confidence_label="high",
            data_confidence=0.7,
            data_confidence_label="high",
            model_confidence=0.7,
            model_confidence_label="high",
            explanation="baseline",
            model_inputs={},
            model_version="elo-poisson-v1",
        )
    )


def _seed_ai(
    session,
    match_id: str,
    model_version: str,
    home: float | None,
    draw: float | None,
    away: float | None,
    *,
    prompt_version: str = "worldcup-ai-v1",
    error_code: str | None = None,
    created_at: datetime | None = None,
) -> None:
    session.add(
        AIPrediction(
            match_id=match_id,
            provider="deepseek",
            model_id=model_version,
            model_version=model_version,
            prompt_version=prompt_version,
            input_snapshot_json={},
            raw_response_text="{}",
            raw_response_json={},
            parsed_home_win=home,
            parsed_draw=draw,
            parsed_away_win=away,
            confidence=0.8,
            risk_flags_json=[],
            key_factors_json=[],
            reason="ai",
            uncertainties_json=[],
            disagreement_with_system="",
            disagreement_with_market="",
            recommended_label="home_win",
            created_at=created_at or datetime(2026, 6, 14, 9, tzinfo=timezone.utc),
            error_code=error_code,
        )
    )


def test_ai_identical_bucket(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.60, 0.25, 0.15)
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["summary"]["audited_prediction_count"] == 1
    assert result["summary"]["buckets"]["identical"]["count"] == 1
    assert result["records"][0]["bucket"] == "identical"


def test_ai_slight_bucket(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.62, 0.24, 0.14)
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["records"][0]["bucket"] == "slight"


def test_ai_moderate_bucket(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.65, 0.21, 0.14)
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["records"][0]["bucket"] == "moderate"


def test_ai_strong_bucket(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.50, 0.30, 0.20)
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["records"][0]["bucket"] == "strong"


def test_failed_prediction_is_excluded(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.50, 0.30, 0.20, error_code="parse_failed")
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["summary"]["total_valid_ai_prediction_count"] == 0
    assert result["summary"]["audited_prediction_count"] == 0


def test_model_version_group_stats(db_session):
    _seed_match(db_session)
    _seed_match(db_session, "match-2", "B", "CCC", "DDD")
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_baseline(db_session, revision.id, "match-2", 0.55, 0.25, 0.20)
    _seed_ai(db_session, "match-1", "ai-a", 0.62, 0.24, 0.14)
    _seed_ai(db_session, "match-2", "ai-b", 0.45, 0.30, 0.25)
    db_session.flush()

    result = analyze_ai_independence(db_session)
    by_model = result["by_model_version"]

    assert by_model["ai-a"]["average_max_abs_delta"] == 0.02
    assert by_model["ai-b"]["average_max_abs_delta"] == 0.1


def test_direction_same_is_computed_correctly(db_session):
    _seed_match(db_session)
    revision = _seed_active_revision(db_session)
    _seed_baseline(db_session, revision.id, "match-1", 0.60, 0.25, 0.15)
    _seed_ai(db_session, "match-1", "ai-a", 0.20, 0.25, 0.55)
    db_session.flush()

    result = analyze_ai_independence(db_session)

    assert result["records"][0]["direction_same"] is False
    assert result["by_model_version"]["ai-a"]["direction_same_rate"] == 0.0
