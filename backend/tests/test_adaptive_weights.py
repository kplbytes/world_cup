from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.models import AIPrediction, DashboardRevision, MarketSnapshot, Match, PredictionSnapshot, Team, TeamRating
from app.services import adaptive_weights as adaptive_weights_service
from app.services.adaptive_weights import _collect_per_match_briers, compute_adaptive_weights, get_current_adaptive_weights


def _seed_team(session, team_id: str, name: str, group_code: str) -> None:
    session.add(Team(id=team_id, name=name, short_name=name, code=team_id, group_code=group_code))
    session.add(TeamRating(team_id=team_id, effective_date=date(2026, 6, 1), elo=1500.0, source="test"))


def _seed_match_bundle(
    session,
    match_id: str,
    kickoff: datetime,
    home_score: int,
    away_score: int,
    *,
    add_xiaomi: bool = False,
    add_post_match_prediction: bool = False,
) -> None:
    revision = DashboardRevision(
        active=True,
        model_version="elo-poisson-v1",
        simulation_iterations=1,
        simulation_seed=1,
    )
    session.add(revision)
    session.flush()

    match = Match(
        id=match_id,
        group_code="A",
        stage="group",
        home_team_id="HOME",
        away_team_id="AWAY",
        kickoff=kickoff,
        status="final",
        source="test",
        home_score=home_score,
        away_score=away_score,
    )
    session.add(match)
    session.flush()

    session.add(
        PredictionSnapshot(
            match_id=match_id,
            revision_id=revision.id,
            kickoff=kickoff,
            snapshotted_at=kickoff - timedelta(hours=2),
            is_pre_match_locked=True,
            home_win=0.55,
            draw=0.25,
            away_win=0.20,
            home_xg=1.5,
            away_xg=0.8,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="elo-poisson-v1",
        )
    )

    session.add(
        MarketSnapshot(
            match_id=match_id,
            provider="sporttery",
            fetched_at=kickoff - timedelta(hours=1),
            home_probability=0.52,
            draw_probability=0.26,
            away_probability=0.22,
            raw_overround=1.05,
            source_match_id=f"sporttery-{match_id}",
        )
    )

    if add_post_match_prediction:
        session.add(
            AIPrediction(
                match_id=match_id,
                provider="deepseek",
                model_id="deepseek-v4-flash",
                model_version="ai-deepseek-v4-flash-v1",
                prompt_version="worldcup-ai-v1",
                parsed_home_win=1.0,
                parsed_draw=0.0,
                parsed_away_win=0.0,
                confidence=0.95,
                is_pre_match_locked=False,
                is_fallback_locked=False,
                created_at=kickoff + timedelta(minutes=15),
            )
        )

    session.add(
        AIPrediction(
            match_id=match_id,
            provider="deepseek",
            model_id="deepseek-v4-flash",
            model_version="ai-deepseek-v4-flash-v1",
            prompt_version="worldcup-ai-v1",
            parsed_home_win=0.60,
            parsed_draw=0.20,
            parsed_away_win=0.20,
            confidence=0.75,
            is_pre_match_locked=True,
            is_fallback_locked=False,
            created_at=kickoff - timedelta(hours=1),
        )
    )

    if add_xiaomi:
        session.add(
            AIPrediction(
                match_id=match_id,
                provider="xiaomi",
                model_id="mimo-v2.5-pro",
                model_version="ai-xiaomi-mimo-v2.5-pro-v1",
                prompt_version="worldcup-ai-v1",
                parsed_home_win=0.61,
                parsed_draw=0.19,
                parsed_away_win=0.20,
                confidence=0.74,
                is_pre_match_locked=True,
                is_fallback_locked=False,
                created_at=kickoff - timedelta(hours=1),
            )
        )


def test_compute_adaptive_weights_uses_match_samples_and_hides_xiaomi(db_session):
    _seed_team(db_session, "HOME", "Home", "A")
    _seed_team(db_session, "AWAY", "Away", "A")
    db_session.flush()

    kickoff_base = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    for i in range(10):
        _seed_match_bundle(
            db_session,
            f"m_adaptive_{i}",
            kickoff_base - timedelta(days=i),
            home_score=1,
            away_score=0,
            add_xiaomi=True,
        )
    db_session.flush()

    result = compute_adaptive_weights(db_session)

    assert result["is_adaptive"] is True
    assert result["performance"]["system"]["sample_count"] == 10
    assert result["performance"]["market"]["sample_count"] == 10
    assert result["performance"]["ai_ai-deepseek-v4-flash-v1"]["sample_count"] == 10
    assert "ai_ai-xiaomi-mimo-v2.5-pro-v1" not in result["performance"]
    assert all("xiaomi" not in key and "mimo" not in key for key in result["weights"])


def test_collect_per_match_briers_uses_pre_match_ai_prediction(db_session):
    _seed_team(db_session, "HOME", "Home", "A")
    _seed_team(db_session, "AWAY", "Away", "A")
    db_session.flush()

    kickoff = datetime(2026, 6, 20, 12, tzinfo=timezone.utc)
    _seed_match_bundle(
        db_session,
        "m_locked_selection",
        kickoff,
        home_score=1,
        away_score=0,
        add_post_match_prediction=True,
    )
    db_session.flush()

    per_match_briers, _ = _collect_per_match_briers(db_session)

    assert per_match_briers["ai_ai-deepseek-v4-flash-v1"]["m_locked_selection"] == pytest.approx(0.24)


def test_get_current_adaptive_weights_prefers_cached_state(monkeypatch):
    cached_state = {
        "weights": {"system": 0.2, "market": 0.2, "ai_ai-deepseek-v4-flash-v1": 0.6},
        "performance": {"system": {"sample_count": 54}},
        "is_adaptive": True,
        "significance": {},
        "last_updated": "2026-06-26T10:00:00+00:00",
        "config": {"algorithm": "bayesian_model_averaging_v2"},
    }

    monkeypatch.setattr(adaptive_weights_service, "_load_state", lambda _path: cached_state)

    def _unexpected_compute(_session):
        raise AssertionError("compute_adaptive_weights should not run when cached adaptive state exists")

    monkeypatch.setattr(adaptive_weights_service, "compute_adaptive_weights", _unexpected_compute)
    cached_session = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=SimpleNamespace(database="/tmp/world-cup-cached.sqlite3")))

    assert get_current_adaptive_weights(session=cached_session) == cached_state


def test_state_path_isolated_per_database():
    session_a = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=SimpleNamespace(database="/tmp/world-cup-a.sqlite3")))
    session_b = SimpleNamespace(get_bind=lambda: SimpleNamespace(url=SimpleNamespace(database="/tmp/world-cup-b.sqlite3")))

    path_a = adaptive_weights_service._get_state_path(session_a)
    path_b = adaptive_weights_service._get_state_path(session_b)

    assert path_a != path_b
    assert path_a.name.startswith("adaptive_weights_state_")
    assert path_b.name.startswith("adaptive_weights_state_")
