"""Tests for prompt v1/v2 independence and switching."""

import json
import re
import pytest

from app.ai.prompt_builder import (
    build_prediction_prompt,
    build_prediction_prompt_v2,
    build_prompt,
    analyze_prompt_independence,
)
from app.ai.providers.base import AIPredictionRequest


@pytest.fixture
def sample_request():
    """Create a sample AIPredictionRequest for testing."""
    return AIPredictionRequest(
        match_id="test-match-001",
        stage="group",
        group="A",
        knockout_round=None,
        home_team="Germany",
        away_team="Curacao",
        kickoff="2026-06-15T18:00:00Z",
        venue="Test Stadium",
        neutral_ground=True,
        system_home_win=0.68,
        system_draw=0.21,
        system_away_win=0.11,
        system_home_xg=1.8,
        system_away_xg=0.9,
        system_model_confidence=0.7,
        system_data_confidence=0.8,
        most_likely_score="2-0",
        market_home_prob=0.65,
        market_draw_prob=0.22,
        market_away_prob=0.13,
        market_divergence=0.03,
        market_provider="sporttery",
        market_fetched_at="2026-06-15T10:00:00Z",
        injuries=[{"player": "Player A", "team": "Germany", "type": "muscle"}],
        suspensions=[],
        risk_flags=["injury:Player A"],
        group_standing_context="Germany: 3pts (1st), Curacao: 0pts (4th)",
        knockout_context=None,
        historical_score_summary="elo-poisson-v1: Brier=0.2100, n=20",
        home_team_profile={"traits": ["遇强韧性高"], "sample_count": 10},
        away_team_profile={"traits": ["弱旅虐菜稳定"], "sample_count": 8},
    )


class TestV1PromptContainsBaselineAnchors:
    """Verify v1 prompt DOES contain baseline probability anchors (current behavior)."""

    def test_v1_contains_home_win_probability(self, sample_request):
        prompt = build_prediction_prompt(sample_request, "worldcup-ai-v1")
        assert "home_win_probability" in prompt
        assert "0.68" in prompt  # The actual probability value

    def test_v1_contains_draw_probability(self, sample_request):
        prompt = build_prediction_prompt(sample_request, "worldcup-ai-v1")
        assert "draw_probability" in prompt
        assert "0.21" in prompt

    def test_v1_contains_away_win_probability(self, sample_request):
        prompt = build_prediction_prompt(sample_request, "worldcup-ai-v1")
        assert "away_win_probability" in prompt
        assert "0.11" in prompt

    def test_v1_contains_xg_values(self, sample_request):
        prompt = build_prediction_prompt(sample_request, "worldcup-ai-v1")
        assert "home_xg" in prompt
        assert "away_xg" in prompt

    def test_v1_analysis_detects_anchors(self, sample_request):
        prompt = build_prediction_prompt(sample_request, "worldcup-ai-v1")
        analysis = analyze_prompt_independence(prompt)
        assert analysis["contains_baseline_probabilities"] is True
        assert analysis["anchor_count"] >= 3  # At least 3 probability fields


class TestV2PromptNoBaselineAnchors:
    """Verify v2 prompt does NOT contain baseline probability anchors."""

    def test_v2_no_home_win_probability(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "home_win_probability" not in prompt

    def test_v2_no_draw_probability(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "draw_probability" not in prompt

    def test_v2_no_away_win_probability(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "away_win_probability" not in prompt

    def test_v2_no_raw_xg_values(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        # Should not have "home_xg": 1.8 or "away_xg": 0.9
        assert "home_xg" not in prompt
        assert "away_xg" not in prompt

    def test_v2_no_raw_three_way_probs(self, sample_request):
        """v2 should NOT contain the exact probability triplet 0.68/0.21/0.11."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        # Check that the exact baseline triplet is not present
        assert not re.search(r"0\.68.*0\.21.*0\.11", prompt)

    def test_v2_analysis_no_anchors(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        analysis = analyze_prompt_independence(prompt)
        assert analysis["contains_baseline_probabilities"] is False
        assert analysis["anchor_count"] == 0

    def test_v2_contains_strength_tier(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "strength_tier" in prompt
        assert "strong_favorite" in prompt  # 0.68 - 0.11 = 0.57 > 0.30

    def test_v2_contains_draw_likelihood(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "draw_likelihood" in prompt

    def test_v2_contains_goal_expectation(self, sample_request):
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "goal_expectation" in prompt

    def test_v2_contains_independence_rules(self, sample_request):
        """v2 must contain explicit independence constraints."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "independent" in prompt.lower()
        assert "anchor" in prompt.lower() or "copy" in prompt.lower()
        assert "independence_note" in prompt

    def test_v2_still_has_match_info(self, sample_request):
        """v2 should still include match basic info."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "Germany" in prompt
        assert "Curacao" in prompt
        assert "group" in prompt.lower()

    def test_v2_still_has_market_odds(self, sample_request):
        """v2 should still include market odds (independent signal)."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "Market Odds" in prompt
        assert "0.65" in prompt  # Market home probability

    def test_v2_still_has_intelligence(self, sample_request):
        """v2 should still include intelligence data."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "Intelligence" in prompt
        assert "Player A" in prompt

    def test_v2_still_has_team_profiles(self, sample_request):
        """v2 should still include team profiles."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        assert "Team Profiles" in prompt

    def test_v2_output_compatible_with_parser(self, sample_request):
        """v2 output format must include all fields that the parser expects."""
        prompt = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")
        # Core fields that parse_ai_response() expects
        assert "home_win" in prompt
        assert "draw" in prompt
        assert "away_win" in prompt
        assert "confidence" in prompt
        assert "risk_flags" in prompt
        assert "key_factors" in prompt
        assert "reason" in prompt
        assert "recommended_label" in prompt


class TestPromptDispatch:
    """Test the build_prompt() dispatch function."""

    def test_dispatch_v1(self, sample_request):
        prompt = build_prompt(sample_request, "worldcup-ai-v1")
        # v1 should contain baseline probabilities
        assert "home_win_probability" in prompt

    def test_dispatch_v2(self, sample_request):
        prompt = build_prompt(sample_request, "worldcup-ai-v2")
        # v2 should NOT contain baseline probabilities
        assert "home_win_probability" not in prompt

    def test_dispatch_ai_independent_v2(self, sample_request):
        prompt = build_prompt(sample_request, "ai-independent-v2")
        assert "home_win_probability" not in prompt

    def test_dispatch_unknown_defaults_to_v1(self, sample_request):
        prompt = build_prompt(sample_request, "unknown-version")
        assert "home_win_probability" in prompt

    def test_v2_dispatch_actually_uses_v2_builder(self, sample_request):
        """Verify that build_prompt with v2 version actually calls the v2 builder,
        not the v1 builder with v2 version string."""
        from app.ai.prompt_builder import build_prediction_prompt, build_prediction_prompt_v2

        v2_prompt = build_prompt(sample_request, "worldcup-ai-v2")
        v2_direct = build_prediction_prompt_v2(sample_request, "worldcup-ai-v2")

        # The dispatched v2 prompt must be IDENTICAL to the direct v2 builder output
        assert v2_prompt == v2_direct, "build_prompt('worldcup-ai-v2') must produce the same output as build_prediction_prompt_v2()"

        # And it must NOT be the same as v1
        v1_prompt = build_prompt(sample_request, "worldcup-ai-v1")
        assert v2_prompt != v1_prompt, "v2 prompt must differ from v1 prompt"


class TestAnalyzePromptIndependence:
    """Test the analyze_prompt_independence utility."""

    def test_detects_probability_anchors(self):
        text = 'home_win_probability": 0.68, "draw_probability": 0.21'
        result = analyze_prompt_independence(text)
        assert result["contains_baseline_probabilities"] is True
        assert result["anchor_count"] >= 2

    def test_detects_xg_anchors(self):
        text = '"home_xg": 1.8, "away_xg": 0.9'
        result = analyze_prompt_independence(text)
        assert result["contains_baseline_probabilities"] is True

    def test_no_anchors_in_clean_text(self):
        text = "This is a clean prompt with no probability anchors."
        result = analyze_prompt_independence(text)
        assert result["contains_baseline_probabilities"] is False
        assert result["anchor_count"] == 0

    def test_prompt_length(self):
        text = "Short prompt"
        result = analyze_prompt_independence(text)
        assert result["prompt_length"] == len(text)


class TestV2StrengthTiers:
    """Test that v2 correctly categorizes strength tiers."""

    def test_strong_favorite(self):
        req = AIPredictionRequest(
            match_id="t1", stage="group", group="A", knockout_round=None,
            home_team="A", away_team="B", kickoff="", venue=None, neutral_ground=True,
            system_home_win=0.70, system_draw=0.20, system_away_win=0.10,
            system_home_xg=2.0, system_away_xg=0.7,
            system_model_confidence=0.7, system_data_confidence=0.8,
            most_likely_score="2-0",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
        )
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "strong_favorite" in prompt

    def test_evenly_matched(self):
        req = AIPredictionRequest(
            match_id="t2", stage="group", group="A", knockout_round=None,
            home_team="A", away_team="B", kickoff="", venue=None, neutral_ground=True,
            system_home_win=0.35, system_draw=0.30, system_away_win=0.35,
            system_home_xg=1.2, system_away_xg=1.1,
            system_model_confidence=0.5, system_data_confidence=0.6,
            most_likely_score="1-1",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
        )
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "evenly_matched" in prompt

    def test_away_favorite(self):
        req = AIPredictionRequest(
            match_id="t3", stage="group", group="A", knockout_round=None,
            home_team="A", away_team="B", kickoff="", venue=None, neutral_ground=True,
            system_home_win=0.15, system_draw=0.25, system_away_win=0.60,
            system_home_xg=0.8, system_away_xg=1.6,
            system_model_confidence=0.6, system_data_confidence=0.7,
            most_likely_score="0-1",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
        )
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "moderate_underdog" in prompt or "strong_underdog" in prompt


class TestTeamProfileMockLabel:
    """Test that mock team profiles are labeled in prompts."""

    def _make_request_with_mock_profile(self):
        """Create a request with mock team profile data."""
        return AIPredictionRequest(
            match_id="t-mock", stage="group", group="A", knockout_round=None,
            home_team="A", away_team="B", kickoff="", venue=None, neutral_ground=True,
            system_home_win=0.60, system_draw=0.25, system_away_win=0.15,
            system_home_xg=1.5, system_away_xg=0.8,
            system_model_confidence=0.7, system_data_confidence=0.8,
            most_likely_score="2-0",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
            home_team_profile={
                "traits": ["遇强韧性高"],
                "sample_count": 20,
                "draw_resilience_score": 0.6,
                "favorite_overconfidence_risk": 0.3,
                "low_score_tendency": 0.4,
                "summary": "基于 20 场正式比赛",
                "profile_version": "team-profile-v1",
                "profile_as_of": "2026-06-01T00:00:00+00:00",
                "source_mode": "seed_mock_v1",
                "sources": ["seed_mock_v1"],
                "is_mock": True,
                "usage_warning": "功能验证数据，不代表真实历史统计；只能作为实验性弱信号，不得作为主要概率调整依据",
            },
            away_team_profile={
                "traits": ["弱旅虐菜稳定"],
                "sample_count": 15,
                "draw_resilience_score": 0.3,
                "favorite_overconfidence_risk": 0.5,
                "low_score_tendency": 0.2,
                "summary": "基于 15 场正式比赛",
                "profile_version": "team-profile-v1",
                "profile_as_of": "2026-06-01T00:00:00+00:00",
                "source_mode": "seed_mock_v1",
                "sources": ["seed_mock_v1"],
                "is_mock": True,
                "usage_warning": "功能验证数据，不代表真实历史统计；只能作为实验性弱信号，不得作为主要概率调整依据",
            },
        )

    def test_v1_prompt_contains_seed_mock_v1(self):
        """v1 prompt should contain seed_mock_v1 label when profile is mock."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt(req, "worldcup-ai-v1")
        assert "seed_mock_v1" in prompt, "v1 prompt should contain seed_mock_v1 source_mode"

    def test_v2_prompt_contains_seed_mock_v1(self):
        """v2 prompt should contain seed_mock_v1 label when profile is mock."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "seed_mock_v1" in prompt, "v2 prompt should contain seed_mock_v1 source_mode"

    def test_v1_prompt_contains_is_mock(self):
        """v1 prompt should contain is_mock flag."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt(req, "worldcup-ai-v1")
        assert "is_mock" in prompt, "v1 prompt should contain is_mock field"

    def test_v2_prompt_contains_is_mock(self):
        """v2 prompt should contain is_mock flag."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "is_mock" in prompt, "v2 prompt should contain is_mock field"

    def test_v1_prompt_contains_usage_warning(self):
        """v1 prompt should contain usage_warning for mock profiles."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt(req, "worldcup-ai-v1")
        assert "功能验证数据" in prompt, "v1 prompt should contain usage_warning"

    def test_v2_prompt_contains_usage_warning(self):
        """v2 prompt should contain usage_warning for mock profiles."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "功能验证数据" in prompt, "v2 prompt should contain usage_warning"

    def test_v1_prompt_has_mock_constraint_rule(self):
        """v1 prompt should have rule about is_mock profiles."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt(req, "worldcup-ai-v1")
        assert "is_mock=true" in prompt, "v1 prompt should have is_mock constraint rule"

    def test_v2_prompt_has_mock_constraint_rule(self):
        """v2 prompt should have rule about is_mock profiles."""
        req = self._make_request_with_mock_profile()
        prompt = build_prediction_prompt_v2(req, "worldcup-ai-v2")
        assert "is_mock=true" in prompt, "v2 prompt should have is_mock constraint rule"

    def test_non_mock_profile_no_warning(self):
        """Non-mock profiles should not have usage_warning."""
        req = AIPredictionRequest(
            match_id="t-real", stage="group", group="A", knockout_round=None,
            home_team="A", away_team="B", kickoff="", venue=None, neutral_ground=True,
            system_home_win=0.60, system_draw=0.25, system_away_win=0.15,
            system_home_xg=1.5, system_away_xg=0.8,
            system_model_confidence=0.7, system_data_confidence=0.8,
            most_likely_score="2-0",
            market_home_prob=None, market_draw_prob=None, market_away_prob=None,
            market_divergence=None, market_provider=None, market_fetched_at=None,
            home_team_profile={
                "traits": ["遇强韧性高"],
                "sample_count": 20,
                "source_mode": "real",
                "sources": ["api-football"],
                "is_mock": False,
                "usage_warning": None,
            },
            away_team_profile=None,
        )
        prompt = build_prediction_prompt(req, "worldcup-ai-v1")
        assert "seed_mock_v1" not in prompt or "is_mock" in prompt
        # The profile should show is_mock=false, not the mock warning
        profile_section = prompt[prompt.index("Team Profiles"):]
        assert '"is_mock": false' in profile_section or '"is_mock":False' in profile_section
