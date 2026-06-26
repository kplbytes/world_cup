from datetime import date, datetime, timedelta, timezone

from sqlalchemy import event, select

from app.models import DashboardRevision, Match, PredictionSnapshot, Team, TeamProfile, TeamProfileMatchHistory, TeamProfilePrediction, TeamRating
from app.team_profiles.feature_engineering import classify_opponent_tier
from app.team_profiles.evaluation import evaluate_profile_model
from app.team_profiles.scorer import apply_profile_adjustment
from app.team_profiles.data_loader import load_profile_match_history_snapshot, load_world_cup_history_snapshot
import app.team_profiles.service as team_profile_service
from app.team_profiles.service import compute_team_profile, rebuild_team_profiles, explain_team_profile, profile_payload


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


def _count_selects(session, fn):
    engine = session.get_bind()
    count = 0

    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal count
        if statement.lstrip().upper().startswith("SELECT"):
            count += 1

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        result = fn()
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)
    return result, count


def _add_profile_prediction(
    session,
    *,
    revision_id: int,
    match_id: str,
    kickoff: datetime,
    home_win: float,
    draw: float,
    away_win: float,
    triggered_traits: list[str],
    created_at: datetime,
    is_pre_match_locked: bool = False,
    is_fallback_locked: bool = False,
):
    session.add(TeamProfilePrediction(
        revision_id=revision_id,
        match_id=match_id,
        model_version="elo-poisson-v1-team-profile",
        profile_version="test-profile-v1",
        profile_as_of=kickoff - timedelta(days=1),
        base_home_win=0.5,
        base_draw=0.25,
        base_away_win=0.25,
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        home_xg=1.1,
        away_xg=0.9,
        probability_deltas_json={},
        xg_deltas_json={},
        risk_flags_json=[],
        triggered_traits_json=triggered_traits,
        explanation="test profile prediction",
        is_pre_match_locked=is_pre_match_locked,
        is_fallback_locked=is_fallback_locked,
        real_time_only=False,
        locked_at=created_at if (is_pre_match_locked or is_fallback_locked) else None,
        created_at=created_at,
    ))


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
    assert "防线稳固" in profile.traits_json
    assert "进攻哑火风险" in profile.traits_json
    assert len(profile.traits_json) >= 4


def test_profile_traits_cover_attack_tempo_and_consistency(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=3 if index < 6 else 2, goals_against=1 if index % 3 == 0 else 0,
            result="win", points=3, is_world_cup=False, is_qualifier=True,
            is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert "进攻火力顶级" in profile.traits_json
    assert "稳定破门" in profile.traits_json
    assert "开放对攻倾向" in profile.traits_json
    assert "近期结果稳定" in profile.traits_json
    assert "预选赛抢分强" in profile.traits_json
    assert len(profile.traits_json) >= 5


def test_high_scoring_low_elo_team_is_not_labeled_elite_attack(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="B", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Weak {index}",
            opponent_elo=1300, opponent_tier="weak", is_neutral=True, is_home=False,
            goals_for=3, goals_against=1, result="win", points=3,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "B", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert "进攻产量高" in profile.traits_json
    assert "进攻火力顶级" not in profile.traits_json


def test_balanced_profile_still_gets_descriptive_traits(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2 if index % 2 == 0 else 1, goals_against=1,
            result="win" if index % 2 == 0 else "draw", points=3 if index % 2 == 0 else 1,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 1, 1, tzinfo=timezone.utc))

    assert "进攻输出中等" in profile.traits_json
    assert "防守中等稳健" in profile.traits_json
    assert "节奏均衡" in profile.traits_json


def test_profile_explanation_summarizes_profile_dimensions(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Strong {index}",
            opponent_elo=1800, opponent_tier="strong", is_neutral=True, is_home=False,
            goals_for=1, goals_against=0 if index < 6 else 1,
            result="win" if index < 4 else "draw", points=3 if index < 4 else 1,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 1, 1, tzinfo=timezone.utc))
    explanation = explain_team_profile(profile)

    assert "正式比赛" in explanation
    assert "预选赛" in explanation
    assert "零封" in explanation
    assert "低比分" in explanation
    assert "遇强不败" in explanation


def test_profile_payload_contains_seven_structured_modules_and_data_quality(db_session):
    _teams(db_session)
    for index in range(10):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2 if index % 2 == 0 else 1, goals_against=0 if index < 6 else 1,
            result="win" if index < 7 else "draw", points=3 if index < 7 else 1,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 1, 1, tzinfo=timezone.utc))
    payload = profile_payload(profile)

    assert payload["usage_scope"] == "display_only"
    assert payload["prediction_enabled"] is False
    assert payload["long_term_strength_score"] > 0
    assert payload["recent_form_score"] > 0
    assert payload["attack_score"] > 0
    assert payload["defense_score"] > 0
    assert payload["stability_score"] > 0
    assert payload["tournament_experience_score"] >= 0
    assert payload["lineup_integrity_score"] is None
    assert payload["injury_risk_score"] is None
    assert payload["rest_days"] is None
    assert payload["schedule_fatigue_score"] is None
    assert payload["environment_adaptation_score"] is None
    assert set(payload["profile_modules_json"]) == {
        "long_term_strength",
        "recent_form",
        "attack_defense",
        "tactical_style",
        "lineup_players",
        "environment",
        "data_quality",
    }
    assert payload["lineup_integrity_status"] == "unavailable"
    assert payload["environment_adaptation_status"] == "unavailable"
    assert "lineup_integrity_score" in payload["missing_fields"]
    assert payload["source_list"] == ["test"]
    assert set(payload["team_profile_narrative"]) >= {
        "long_term_strength",
        "recent_form",
        "attack_defense",
        "tactical_style",
        "lineup_players",
        "environment",
        "data_quality",
    }
    assert payload["data_quality_json"]["quality_penalties"]["lineup_player_unavailable"] > 0
    assert payload["data_quality_json"]["quality_penalties"]["schedule_environment_unavailable"] > 0
    assert payload["data_quality_json"]["quality_penalties"]["climate_venue_unavailable"] > 0
    assert payload["data_quality_score"] < 65


def test_profile_payload_traces_elo_and_fifa_ranking_sources(db_session):
    session = db_session
    session.add_all([
        Team(id="C", name="Charlie", short_name="Charlie", code="CHA", group_code="A"),
        Team(id="D", name="Delta", short_name="Delta", code="DEL", group_code="A"),
    ])
    session.add_all([
        TeamRating(
            team_id="C",
            effective_date=date(2026, 6, 11),
            elo=1750,
            fifa_rank=24,
            fifa_points=1588.4,
            source="world_football_elo+fifa_official",
        ),
        TeamRating(team_id="D", effective_date=date(2026, 6, 11), elo=1450, source="test"),
    ])
    for index in range(10):
        session.add(TeamProfileMatchHistory(
            team_id="C", match_date=date(2025, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2, goals_against=1, result="win" if index < 6 else "draw", points=3 if index < 6 else 1,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="historical_real",
        ))
    session.flush()

    profile = compute_team_profile(session, "C", datetime(2026, 6, 19, tzinfo=timezone.utc))
    payload = profile_payload(profile)

    long_term = payload["profile_modules_json"]["long_term_strength"]
    quality = payload["team_profile_data_quality"]
    assert long_term["fifa_rank"] == 24
    assert long_term["fifa_points"] == 1588.4
    assert long_term["rating_source"] == "world_football_elo+fifa_official"
    assert "fifa_rank" not in payload["missing_fields"]
    assert not any("fifa_rank_missing" == key for key in quality["quality_penalties"])
    assert any(item.startswith("elo:world_football_elo+fifa_official:2026-06-11") for item in payload["source_list"])
    assert any(item.startswith("fifa_ranking:world_football_elo+fifa_official:2026-06-11") for item in payload["source_list"])


def test_profile_environment_uses_verified_schedule_rest_and_venues(db_session, monkeypatch):
    monkeypatch.setattr(team_profile_service, "_venue_climate", lambda: {
        "source": {
            "provider": "open_meteo_historical_archive",
            "source_url": "https://archive-api.open-meteo.com/v1/archive",
            "years": [2015, 2024],
        },
        "venues": {
            "Toronto": {
                "baseline_by_month": {
                    "6": {
                        "sample_days": 300,
                        "temperature_2m_mean_c": 19.2,
                        "temperature_2m_max_mean_c": 24.1,
                        "temperature_2m_min_mean_c": 14.6,
                        "relative_humidity_2m_mean_pct": 72.0,
                        "precipitation_sum_mean_mm": 2.1,
                        "rain_day_rate": 0.31,
                        "wind_speed_10m_max_mean_kmh": 18.0,
                    }
                }
            }
        },
    })
    _teams(db_session)
    db_session.add_all([
        Match(
            id="A-previous",
            group_code="A",
            home_team_id="A",
            away_team_id="B",
            kickoff=datetime(2026, 6, 10, 18, tzinfo=timezone.utc),
            venue="Mexico City",
            status="final",
            home_score=1,
            away_score=0,
            source="fixture_seed",
        ),
        Match(
            id="A-next",
            group_code="A",
            home_team_id="B",
            away_team_id="A",
            kickoff=datetime(2026, 6, 15, 18, tzinfo=timezone.utc),
            venue="Toronto",
            status="scheduled",
            source="fixture_seed",
        ),
        Match(
            id="A-later",
            group_code="A",
            home_team_id="A",
            away_team_id="B",
            kickoff=datetime(2026, 6, 21, 18, tzinfo=timezone.utc),
            venue="Atlanta",
            status="scheduled",
            source="fixture_seed",
        ),
    ])
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2025, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=1, goals_against=0, result="win", points=3,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 6, 12, tzinfo=timezone.utc))
    payload = profile_payload(profile)
    environment = payload["profile_modules_json"]["environment"]

    assert payload["rest_days"] == 5.0
    assert payload["schedule_fatigue_score"] == 80.0
    assert environment["status"] == "partial"
    assert environment["next_match"]["venue"] == "Toronto"
    assert environment["upcoming_venues"] == ["Toronto", "Atlanta"]
    assert 3200 <= environment["travel_distance_km"] <= 3350
    assert environment["timezone_shift_hours"] == 2.0
    assert environment["previous_venue"]["venue"] == "Mexico City"
    assert environment["next_venue"]["timezone"] == "America/Toronto"
    assert payload["environment_adaptation_score"] is not None
    assert environment["climate_adaptation"] != "unavailable"
    assert environment["climate_adaptation"]["source"] == "open_meteo_historical_archive"
    assert environment["climate_adaptation"]["type"] == "historical_climate_baseline"
    assert environment["climate_adaptation"]["is_match_forecast"] is False
    assert "climate:open_meteo_historical_archive" in payload["source_list"]
    assert "rest_days" not in payload["missing_fields"]
    assert "schedule_fatigue_score" not in payload["missing_fields"]
    assert "travel_distance" not in payload["missing_fields"]
    assert "timezone_shift" not in payload["missing_fields"]
    assert "climate_adaptation" not in payload["missing_fields"]


def test_profile_attack_defense_uses_verified_statsbomb_xg(db_session, monkeypatch):
    monkeypatch.setattr(team_profile_service, "_statsbomb_xg_for_team", lambda team: {
        "source": "statsbomb_open_data",
        "competition": "World Cup",
        "seasons": ["2018", "2022"],
        "sample_count": 5,
        "xg_for_avg": 1.42,
        "xg_against_avg": 0.88,
    })
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2025, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2, goals_against=1, result="win", points=3,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 6, 19, tzinfo=timezone.utc))
    payload = profile_payload(profile)
    xg = payload["profile_modules_json"]["attack_defense"]["xg"]

    assert xg["source"] == "statsbomb_open_data"
    assert xg["competition"] == "World Cup"
    assert xg["seasons"] == ["2018", "2022"]
    assert xg["sample_count"] == 5
    assert xg["xg_for_avg"] == 1.42
    assert xg["xg_against_avg"] == 0.88
    assert "xg" not in payload["missing_fields"]
    assert "statsbomb_xg:open_data_world_cup:2018_2022" in payload["source_list"]


def test_profile_attack_defense_keeps_xg_unavailable_when_not_covered(db_session, monkeypatch):
    monkeypatch.setattr(team_profile_service, "_statsbomb_xg_for_team", lambda team: None)
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2025, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2, goals_against=1, result="win", points=3,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 6, 19, tzinfo=timezone.utc))
    payload = profile_payload(profile)

    assert payload["profile_modules_json"]["attack_defense"]["xg"] == "unavailable"
    assert "xg" in payload["missing_fields"]
    assert "statsbomb_xg:open_data_world_cup:2018_2022" not in payload["source_list"]


def test_profile_lineup_players_uses_official_squad_without_injury_or_lineup_claims(db_session, monkeypatch):
    monkeypatch.setattr(team_profile_service, "_fifa_squad_for_team", lambda team: {
        "squad_size": 26,
        "position_counts": {"GK": 3, "DF": 8, "MF": 8, "FW": 7},
        "total_caps": 1267,
        "total_goals": 226,
        "average_caps": 48.7,
        "average_height_cm": 179.6,
        "top_scorers_in_squad": [{"name": "MESSI Lionel", "shirt_name": "MESSI", "goals": 120, "caps": 200}],
        "most_capped_players": [{"name": "MESSI Lionel", "shirt_name": "MESSI", "goals": 120, "caps": 200}],
    })
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2025, 1, 1) + timedelta(days=index * 20),
            competition="qualifier", stage="group", opponent_name=f"Mid {index}",
            opponent_elo=1550, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=2, goals_against=1, result="win", points=3,
            is_world_cup=False, is_qualifier=True, is_friendly=False, source="test",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "A", datetime(2026, 6, 21, tzinfo=timezone.utc))
    payload = profile_payload(profile)
    lineup = payload["profile_modules_json"]["lineup_players"]

    assert lineup["status"] == "official_squad_available"
    assert lineup["squad_size"] == 26
    assert lineup["bench_depth"]["position_counts"]["GK"] == 3
    assert lineup["top_scorer_status"] == "in_official_squad"
    assert lineup["injury_risk_score"] is None
    assert lineup["confirmed_lineup_level"] == "unavailable"
    assert "core_player_dependency" not in payload["missing_fields"]
    assert "bench_depth" not in payload["missing_fields"]
    assert "top_scorer_status" not in payload["missing_fields"]
    assert "injury_risk_score" in payload["missing_fields"]
    assert "confirmed_lineup_level" in payload["missing_fields"]
    assert "fifa_squad:fifa_official_squad_list:2026-06-20" in payload["source_list"]


def test_profile_data_quality_marks_mock_and_missing_fields(db_session):
    _teams(db_session)
    for index in range(8):
        db_session.add(TeamProfileMatchHistory(
            team_id="B", match_date=date(2024, 1, 1) + timedelta(days=index * 20),
            competition="friendly", stage="group", opponent_name=f"Mock {index}",
            opponent_elo=1500, opponent_tier="mid", is_neutral=True, is_home=False,
            goals_for=1, goals_against=1, result="draw", points=1,
            is_world_cup=False, is_qualifier=False, is_friendly=False, source="seed_mock_v1",
        ))
    db_session.flush()

    profile = compute_team_profile(db_session, "B", datetime(2026, 1, 1, tzinfo=timezone.utc))
    payload = profile_payload(profile)

    assert payload["data_quality_json"]["contains_mock"] is True
    assert payload["data_quality_json"]["quality_label"] == "low"
    assert payload["data_quality_score"] < 40
    assert payload["long_term_strength_score"] is None
    assert payload["recent_form_score"] is None
    assert payload["attack_score"] is None
    assert payload["defense_score"] is None
    assert payload["traits_json"] == []
    assert "mock_data_present" in payload["risk_flags"]
    assert payload["prediction_enabled"] is False
    assert payload["profile_modules_json"]["long_term_strength"]["status"] == "mock_data_unavailable"


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


def test_world_cup_history_snapshot_has_source_provenance_and_verified_results():
    snapshot = load_world_cup_history_snapshot()
    assert snapshot["source"]["provider"] == "martj42/international_results"
    assert snapshot["source"]["raw_sha256"] == "9060487bc15858d86e6d47e503004f3fff232568d0910117ca4c6f8998206c58"
    assert snapshot["coverage"] == {
        "competition": "FIFA World Cup",
        "years": [2014, 2018, 2022],
        "matches_per_year": {"2014": 64, "2018": 64, "2022": 64},
        "match_count": 192,
    }

    matches = snapshot["matches"]
    assert len(matches) == 192
    assert {
        "date": "2014-07-08",
        "home_team": "Brazil",
        "away_team": "Germany",
        "home_score": 1,
        "away_score": 7,
        "stage": "knockout",
    }.items() <= next(match for match in matches if match["date"] == "2014-07-08").items()
    assert {
        "date": "2022-12-18",
        "home_team": "Argentina",
        "away_team": "France",
        "home_score": 3,
        "away_score": 3,
        "stage": "knockout",
    }.items() <= matches[-1].items()


def test_profile_match_history_snapshot_is_real_recent_finished_data():
    snapshot = load_profile_match_history_snapshot()
    assert snapshot["source"]["provider"] == "martj42/international_results"
    assert snapshot["source"]["raw_sha256"] == "9060487bc15858d86e6d47e503004f3fff232568d0910117ca4c6f8998206c58"
    assert snapshot["coverage"]["date_start"] == "2022-01-01"
    assert snapshot["coverage"]["date_end"] == "2026-06-19"
    assert snapshot["coverage"]["team_count"] == 48
    assert snapshot["coverage"]["min_matches_per_team"] >= 40
    assert snapshot["coverage"]["match_count"] > 1000

    assert all(isinstance(match["home_score"], int) and isinstance(match["away_score"], int) for match in snapshot["matches"])
    assert all(match["date"] <= "2026-06-19" for match in snapshot["matches"])
    assert any(match["competition"] == "FIFA World Cup qualification" for match in snapshot["matches"])
    assert any(match["competition"] == "Friendly" for match in snapshot["matches"])


def test_rebuild_creates_profile_for_every_team(db_session):
    _teams(db_session)
    result = rebuild_team_profiles(db_session, use_seed=True)
    profiles = list(db_session.scalars(select(TeamProfile)))
    histories = list(db_session.scalars(select(TeamProfileMatchHistory)))
    assert result["profiles"] == 2
    assert len(profiles) == 2
    assert len(histories) >= 24
    assert all(profile.source_summary_json["mode"] == "seed_mock_v1" for profile in profiles)


def test_rebuild_uses_real_history_without_mock_when_recent_samples_are_sufficient(db_session):
    db_session.add_all([
        Team(id="BRA", name="Brazil", short_name="Brazil", code="BRA", group_code="A"),
        Team(id="QAT", name="Qatar", short_name="Qatar", code="QAT", group_code="A"),
    ])
    db_session.add_all([
        TeamRating(team_id="BRA", effective_date=date(2025, 1, 1), elo=1900, source="test"),
        TeamRating(team_id="QAT", effective_date=date(2025, 1, 1), elo=1400, source="test"),
    ])
    db_session.add(TeamProfileMatchHistory(
        team_id="BRA", match_date=date(2024, 1, 1), competition="world_cup", stage="group",
        opponent_name="Old Mock", opponent_elo=1500, opponent_tier="mid", is_neutral=True,
        is_home=False, goals_for=1, goals_against=0, result="win", points=3,
        is_world_cup=True, is_qualifier=False, is_friendly=False, source="seed_mock_v1",
    ))
    db_session.flush()

    result = rebuild_team_profiles(db_session, use_seed=True)

    brazil_profile = db_session.scalar(select(TeamProfile).where(TeamProfile.team_id == "BRA"))
    qatar_profile = db_session.scalar(select(TeamProfile).where(TeamProfile.team_id == "QAT"))

    assert result["data_mode"] == "historical_real_with_seed_fallback"
    assert brazil_profile.source_summary_json["mode"] == "historical_real"
    assert brazil_profile.source_summary_json["sources"] == ["historical_real"]
    assert brazil_profile.source_summary_json["provider"] == "martj42/international_results"
    assert brazil_profile.source_summary_json["raw_sha256"] == "9060487bc15858d86e6d47e503004f3fff232568d0910117ca4c6f8998206c58"
    assert brazil_profile.source_summary_json["date_start"] == "2022-01-01"
    assert brazil_profile.source_summary_json["date_end"] == "2026-06-19"
    assert qatar_profile.source_summary_json["mode"] == "historical_real"
    assert qatar_profile.source_summary_json["sources"] == ["historical_real"]


def test_evaluate_profile_model_batches_queries_and_preserves_effect_counts(db_session):
    _teams(db_session)
    db_session.add_all([
        Team(id="C", name="Charlie", short_name="Charlie", code="CHA", group_code="A"),
        Team(id="D", name="Delta", short_name="Delta", code="DEL", group_code="A"),
    ])
    revision = DashboardRevision(active=True, model_version="v1", simulation_iterations=1, simulation_seed=1)
    db_session.add(revision)
    db_session.flush()

    kickoff_1 = datetime(2026, 6, 20, 12, 0, tzinfo=timezone.utc)
    kickoff_2 = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)
    db_session.add_all([
        Match(id="profile_m1", group_code="A", home_team_id="A", away_team_id="B", kickoff=kickoff_1, status="final", source="test", home_score=1, away_score=1),
        Match(id="profile_m2", group_code="A", home_team_id="C", away_team_id="D", kickoff=kickoff_2, status="final", source="test", home_score=1, away_score=0),
    ])
    db_session.add_all([
        PredictionSnapshot(
            match_id="profile_m1",
            revision_id=revision.id,
            kickoff=kickoff_1,
            snapshotted_at=kickoff_1 - timedelta(hours=2),
            home_win=0.6,
            draw=0.2,
            away_win=0.2,
            home_xg=1.0,
            away_xg=0.8,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="v1",
        ),
        PredictionSnapshot(
            match_id="profile_m2",
            revision_id=revision.id,
            kickoff=kickoff_2,
            snapshotted_at=kickoff_2 - timedelta(hours=2),
            home_win=0.7,
            draw=0.2,
            away_win=0.1,
            home_xg=1.4,
            away_xg=0.7,
            scorelines=[],
            score_matrix=[],
            confidence=0.8,
            confidence_label="High",
            model_inputs={},
            model_version="v1",
        ),
    ])
    _add_profile_prediction(
        db_session,
        revision_id=revision.id,
        match_id="profile_m1",
        kickoff=kickoff_1,
        home_win=0.3,
        draw=0.5,
        away_win=0.2,
        triggered_traits=["遇强韧性高"],
        created_at=kickoff_1 - timedelta(minutes=30),
        is_pre_match_locked=True,
    )
    _add_profile_prediction(
        db_session,
        revision_id=revision.id,
        match_id="profile_m2",
        kickoff=kickoff_2,
        home_win=0.4,
        draw=0.3,
        away_win=0.3,
        triggered_traits=["节奏均衡"],
        created_at=kickoff_2 - timedelta(minutes=20),
        is_fallback_locked=True,
    )
    db_session.flush()

    result, select_count = _count_selects(db_session, lambda: evaluate_profile_model(db_session))

    assert select_count <= 4
    assert result["sample_count"] == 2
    assert result["helped"] == 1
    assert result["hurt"] == 1
    assert result["neutral"] == 0
    assert result["most_helpful_traits"] == [{"trait": "遇强韧性高", "count": 1}]
    assert result["most_misleading_traits"] == [{"trait": "节奏均衡", "count": 1}]
