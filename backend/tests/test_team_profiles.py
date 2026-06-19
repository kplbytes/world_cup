from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models import Match, Team, TeamProfile, TeamProfileMatchHistory, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier
from app.team_profiles.scorer import apply_profile_adjustment
from app.team_profiles.service import compute_team_profile, rebuild_team_profiles


def _teams(session):
    session.add_all([
        Team(id="A", name="Alpha", short_name="Alpha", code="ALP", group_code="A"),
        Team(id="B", name="Beta", short_name="Beta", code="BET", group_code="A"),
    ])
    session.add_all([
        TeamRating(team_id="A", effective_date=date(2025, 1, 1), elo=1800, source="test"),
        TeamRating(team_id="B", effective_date=date(2025, 1, 1), elo=1500, source="test"),
    ])
    session.flush()


def test_opponent_tier_boundaries():
    assert classify_opponent_tier(1900) == "elite"
    assert classify_opponent_tier(1750) == "strong"
    assert classify_opponent_tier(1550) == "mid"
    assert classify_opponent_tier(1350) == "weak"


def test_profile_as_of_excludes_future_matches(db_session):
    _teams(db_session)
    cutoff = datetime(2025, 6, 1, tzinfo=timezone.utc)
    db_session.add_all([
        TeamProfileMatchHistory(team_id="A", match_date=date(2025, 1, 1), competition="qualifier",
            stage="group", opponent_name="Elite", opponent_elo=1900, opponent_tier="elite",
            is_neutral=True, is_home=False, goals_for=1, goals_against=1, result="draw", points=1,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test"),
        TeamProfileMatchHistory(team_id="A", match_date=date(2025, 8, 1), competition="qualifier",
            stage="group", opponent_name="Elite", opponent_elo=1900, opponent_tier="elite",
            is_neutral=True, is_home=False, goals_for=0, goals_against=5, result="loss", points=0,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test"),
    ])
    db_session.flush()

    profile = compute_team_profile(db_session, "A", cutoff)
    assert profile.sample_count == 1
    assert profile.goal_against_avg == 1.0
    assert profile.draw_rate_vs_elite == 1.0


def test_profile_metrics_and_traits_require_evidence(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="B", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="world_cup", stage="group", opponent_name=f"Strong {index}",
            opponent_elo=1800, opponent_tier="strong", is_neutral=True, is_home=False,
            goals_for=0 if index % 2 else 1, goals_against=0 if index < 6 else 1,
            result="draw" if index < 6 else "loss", points=1 if index < 6 else 0,
            is_world_cup=True, is_qualifier=False, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "B", datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert profile.draw_resilience_score > 0.6
    assert profile.low_score_tendency > 0.7
    assert "遇强韧性高" in profile.traits_json
    assert "低比分倾向" in profile.traits_json


def test_small_sample_does_not_emit_strong_traits(db_session):
    _teams(db_session)
    db_session.add(TeamProfileMatchHistory(
        team_id="A", match_date=date(2025, 1, 1), competition="world_cup", stage="group",
        opponent_name="Elite", opponent_elo=1900, opponent_tier="elite", is_neutral=True,
        is_home=False, goals_for=0, goals_against=0, result="draw", points=1,
        is_world_cup=True, is_qualifier=False, is_friendly=False, source="test",
    ))
    db_session.flush()
    profile = compute_team_profile(db_session, "A", datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert profile.sample_count == 1
    assert profile.traits_json == []


def test_profile_adjustment_is_capped_and_normalized():
    strong = {"favorite_win_rate": 0.9, "favorite_fail_to_win_rate": 0.1,
              "favorite_overconfidence_risk": 0.1, "draw_resilience_score": 0.1,
              "defensive_resilience_score": 0.5, "low_score_tendency": 0.2}
    resilient = {"favorite_win_rate": 0.2, "favorite_fail_to_win_rate": 0.8,
                 "favorite_overconfidence_risk": 0.8, "draw_resilience_score": 0.9,
                 "defensive_resilience_score": 0.9, "low_score_tendency": 0.9}
    result = apply_profile_adjustment(
        {"home_win": 0.72, "draw": 0.18, "away_win": 0.10, "home_xg": 2.1, "away_xg": 0.6},
        strong, resilient, home_elo=1900, away_elo=1450,
    )
    assert abs(sum(result["probabilities"].values()) - 1.0) < 1e-9
    assert max(abs(v) for v in result["probability_deltas"].values()) <= 0.05
    assert sum(abs(v) for v in result["probability_deltas"].values()) <= 0.080001
    assert abs(result["xg_deltas"]["home"]) <= 0.15
    assert result["model_version"] == "elo-poisson-v1-team-profile"


def test_rebuild_creates_profile_for_every_team(db_session):
    _teams(db_session)
    result = rebuild_team_profiles(db_session, use_seed=True)
    profiles = list(db_session.scalars(select(TeamProfile)))
    histories = list(db_session.scalars(select(TeamProfileMatchHistory)))
    assert result["profiles"] == 2
    assert len(profiles) == 2
    assert len(histories) >= 24
    assert all(profile.source_summary_json["mode"] == "seed_mock_v1" for profile in profiles)


def test_rebuild_keeps_real_history_clean_for_teams_with_enough_real_samples(db_session):
    db_session.add_all([
        Team(id="BRA", name="Brazil", short_name="Brazil", code="BRA", group_code="A"),
        Team(id="QAT", name="Qatar", short_name="Qatar", code="QAT", group_code="A"),
    ])
    db_session.add_all([
        TeamRating(team_id="BRA", effective_date=date(2025, 1, 1), elo=1900, source="test"),
        TeamRating(team_id="QAT", effective_date=date(2025, 1, 1), elo=1400, source="test"),
    ])
    db_session.flush()

    rebuild_team_profiles(db_session, use_seed=True)

    brazil_profile = db_session.scalar(select(TeamProfile).where(TeamProfile.team_id == "BRA"))
    qatar_profile = db_session.scalar(select(TeamProfile).where(TeamProfile.team_id == "QAT"))

    assert brazil_profile.source_summary_json["mode"] == "historical_real"
    assert brazil_profile.source_summary_json["sources"] == ["historical_real"]
    assert qatar_profile.source_summary_json["mode"] == "mixed"
    assert qatar_profile.source_summary_json["sources"] == ["historical_real", "seed_mock_v1"]
