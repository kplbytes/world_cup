"""Tests for P4 features: matchup sparks, context tags, and head-to-head."""
from datetime import date

from app.models import Team, TeamProfile, TeamProfileMatchHistory
from app.prediction.matchup_analyzer import analyze_matchup
from app.services.dashboard import _safe_head_to_head


def _make_profile(
    team_id: str,
    *,
    tags: list[str] | None = None,
    over_2_5: float = 0.45,
    under_2_5: float = 0.55,
    btts: float = 0.50,
    draw_rate: float = 0.25,
    clean_sheet: float = 0.30,
    failed_score: float = 0.20,
    low_score: float = 0.40,
    high_score: float = 0.35,
    strength: float = 0.60,
    upset: float = 0.30,
) -> TeamProfile:
    """Create a minimal TeamProfile for matchup_analyzer tests."""
    return TeamProfile(
        team_id=team_id,
        team_code=team_id[:3].upper(),
        profile_version="test-v1",
        profile_as_of=__import__("datetime").datetime.now(),
        data_cutoff=__import__("datetime").datetime.now(),
        sample_count=100,
        world_cup_sample_count=10,
        qualifier_sample_count=20,
        competitive_sample_count=30,
        attack_strength_recent=1.2,
        defense_strength_recent=0.9,
        goal_for_avg=1.4,
        goal_against_avg=1.1,
        clean_sheet_rate=clean_sheet,
        failed_to_score_rate=failed_score,
        over_2_5_rate=over_2_5,
        under_2_5_rate=under_2_5,
        both_teams_score_rate=btts,
        low_score_tendency=low_score,
        high_score_tendency=high_score,
        draw_rate_overall=draw_rate,
        draw_rate_vs_elite=0.30,
        draw_rate_vs_strong=0.28,
        draw_rate_as_underdog=0.32,
        draw_resilience_score=0.45,
        favorite_win_rate=0.55,
        favorite_fail_to_win_rate=0.20,
        favorite_overconfidence_risk=0.15,
        weak_opponent_upset_risk=0.10,
        underdog_draw_rate=0.25,
        underdog_win_or_draw_rate=0.35,
        upset_potential_score=upset,
        defensive_resilience_score=0.40,
        world_cup_experience_score=0.60,
        knockout_experience_score=0.50,
        recent_tournament_consistency=0.65,
        pressure_match_score=0.55,
        opening_match_slow_start_score=0.20,
        group_stage_consistency=0.70,
        third_match_rotation_risk=0.25,
        must_win_match_performance=0.60,
        long_term_strength_score=strength,
        data_quality_score=0.80,
        tactical_style_tags_json=tags or [],
    )


# ---------------------------------------------------------------------------
# P4-A & P4-B: matchup_analyzer returns sparks and context_tags
# ---------------------------------------------------------------------------

class TestMatchupSparksAndTags:
    def test_open_exchange_produces_sparks_and_goal_festival_tag(self):
        home = _make_profile("A", tags=["开放对攻型"], over_2_5=0.60, high_score=0.55, strength=0.75)
        away = _make_profile("B", tags=["开放对攻型"], over_2_5=0.58, high_score=0.52, strength=0.72)
        result = analyze_matchup(home, away)
        assert "sparks" in result
        assert len(result["sparks"]) > 0
        assert "context_tags" in result
        assert "进球大战预期" in result["context_tags"]
        assert "强强对话" in result["context_tags"]

    def test_defensive_grind_produces_draw_tag_and_sparks(self):
        home = _make_profile("A", tags=["保守低比分型"], under_2_5=0.65, draw_rate=0.35, strength=0.50)
        away = _make_profile("B", tags=["防守反击型"], under_2_5=0.60, draw_rate=0.32, strength=0.48)
        result = analyze_matchup(home, away)
        assert "平局高危" in result["context_tags"]
        assert any("防守" in s or "平局" in s for s in result["sparks"])

    def test_upset_risk_tag_when_upset_potential_high(self):
        home = _make_profile("A", tags=["强压制型"], strength=0.80, upset=0.10)
        away = _make_profile("B", tags=["防守反击型"], strength=0.35, upset=0.70)
        result = analyze_matchup(home, away)
        assert "爆冷风险" in result["context_tags"]
        assert any("爆冷" in s for s in result["sparks"])

    def test_sparks_max_three_items(self):
        home = _make_profile("A", tags=["开放对攻型", "慢热型"], over_2_5=0.60, strength=0.75, upset=0.65)
        away = _make_profile("B", tags=["开放对攻型", "慢热型"], over_2_5=0.58, strength=0.72, upset=0.65)
        result = analyze_matchup(home, away)
        assert len(result["sparks"]) <= 3

    def test_context_tags_deduplicated(self):
        home = _make_profile("A", tags=["开放对攻型", "防守反击型"], strength=0.75)
        away = _make_profile("B", tags=["强压制型"], strength=0.72)
        result = analyze_matchup(home, away)
        tags = result["context_tags"]
        assert len(tags) == len(set(tags)), "context_tags should not contain duplicates"

    def test_none_profiles_return_empty_sparks(self):
        result = analyze_matchup(None, None)
        assert result["sparks"] == []
        assert result["context_tags"] == []

    def test_strength_gap_tag(self):
        home = _make_profile("A", tags=["开放对攻型"], strength=0.85)
        away = _make_profile("B", tags=["防守反击型"], strength=0.30)
        result = analyze_matchup(home, away)
        assert "实力悬殊" in result["context_tags"]


# ---------------------------------------------------------------------------
# P4-C: _safe_head_to_head field normalization
# ---------------------------------------------------------------------------

class TestHeadToHeadNormalization:
    def test_returns_none_when_no_team_ids(self):
        assert _safe_head_to_head(None, None, None) is None
        assert _safe_head_to_head(None, "A", None) is None
        assert _safe_head_to_head(None, None, "B") is None

    def test_returns_none_when_no_history(self, db_session):
        _seed_teams(db_session)
        result = _safe_head_to_head(db_session, "A", "B")
        assert result is None

    def test_home_perspective_rows_preserved(self, db_session):
        _seed_teams(db_session)
        # Home team (A) beat away team (B) 2-1
        db_session.add(TeamProfileMatchHistory(
            team_id="A", match_date=date(2024, 6, 15), competition="Friendly",
            stage="group", opponent_team_id="B", opponent_name="Beta",
            opponent_elo=1500, opponent_tier="middle", is_neutral=True, is_home=True,
            goals_for=2, goals_against=1, result="win", points=3,
            is_world_cup=False, is_qualifier=False, is_friendly=True, source="test",
        ))
        db_session.flush()
        result = _safe_head_to_head(db_session, "A", "B")
        assert result is not None
        assert result["total_matches"] == 1
        assert result["home_wins"] == 1
        assert result["home_losses"] == 0
        match = result["recent_matches"][0]
        assert match["goals_for"] == 2
        assert match["goals_against"] == 1
        assert match["result"] == "win"
        assert match["home_team_id"] == "A"
        assert match["away_team_id"] == "B"

    def test_away_perspective_rows_normalized(self, db_session):
        """When history is stored from away team's perspective, goals and
        result should be flipped to the current match's home-team perspective."""
        _seed_teams(db_session)
        # Away team (B) beat home team (A) 3-0 — stored from B's perspective
        db_session.add(TeamProfileMatchHistory(
            team_id="B", match_date=date(2024, 7, 20), competition="Friendly",
            stage="group", opponent_team_id="A", opponent_name="Alpha",
            opponent_elo=1800, opponent_tier="elite", is_neutral=True, is_home=False,
            goals_for=3, goals_against=0, result="win", points=3,
            is_world_cup=False, is_qualifier=False, is_friendly=True, source="test",
        ))
        db_session.flush()
        result = _safe_head_to_head(db_session, "A", "B")
        assert result is not None
        assert result["total_matches"] == 1
        # From A's perspective: A lost 0-3
        match = result["recent_matches"][0]
        assert match["goals_for"] == 0  # A's goals (flipped from B's goals_against)
        assert match["goals_against"] == 3  # B's goals (flipped from B's goals_for)
        assert match["result"] == "loss"  # A lost (flipped from B's "win")
        assert match["home_team_id"] == "A"
        assert match["away_team_id"] == "B"

    def test_aggregate_stats_only_count_home_perspective(self, db_session):
        _seed_teams(db_session)
        # 2 home-perspective rows (A's history): 1 win, 1 draw
        for gf, ga, result in [(2, 1, "win"), (1, 1, "draw")]:
            db_session.add(TeamProfileMatchHistory(
                team_id="A", match_date=date(2024, 1, 1), competition="Friendly",
                stage="group", opponent_team_id="B", opponent_name="Beta",
                opponent_elo=1500, opponent_tier="middle", is_neutral=True, is_home=True,
                goals_for=gf, goals_against=ga, result=result,
                points=3 if result == "win" else 1,
                is_world_cup=False, is_qualifier=False, is_friendly=True, source="test",
            ))
        # 1 away-perspective row (B's history): B beat A
        db_session.add(TeamProfileMatchHistory(
            team_id="B", match_date=date(2024, 3, 1), competition="Friendly",
            stage="group", opponent_team_id="A", opponent_name="Alpha",
            opponent_elo=1800, opponent_tier="elite", is_neutral=True, is_home=False,
            goals_for=2, goals_against=0, result="win", points=3,
            is_world_cup=False, is_qualifier=False, is_friendly=True, source="test",
        ))
        db_session.flush()
        result = _safe_head_to_head(db_session, "A", "B")
        assert result["total_matches"] == 3
        assert result["home_wins"] == 1  # only home-perspective wins
        assert result["draws"] == 1
        assert result["home_losses"] == 0  # away-perspective loss not counted in home_losses


def _seed_teams(session):
    session.add_all([
        Team(id="A", name="Alpha", short_name="Alpha", code="ALP", group_code="A"),
        Team(id="B", name="Beta", short_name="Beta", code="BET", group_code="A"),
    ])
    session.flush()
