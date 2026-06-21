"""Direct unit tests for parse_ai_response — the core AI LLM output parser."""

from __future__ import annotations

from app.ai.parser import parse_ai_response


def test_valid_json_with_probabilities():
    """A well-formed JSON response with home_win, draw, away_win parses correctly."""
    raw = '{"home_win": 0.5, "draw": 0.3, "away_win": 0.2}'
    parsed, warnings = parse_ai_response(raw)

    assert parsed is not None
    assert abs(parsed.home_win - 0.5) < 1e-6
    assert abs(parsed.draw - 0.3) < 1e-6
    assert abs(parsed.away_win - 0.2) < 1e-6
    assert parsed.was_normalized is False
    # confidence defaults to 0.5 when not provided
    assert parsed.confidence == 0.5
    assert "confidence_missing" in warnings
    assert "low_explainability" in warnings


def test_extra_text_around_json():
    """JSON embedded in surrounding prose text is still extracted."""
    raw = 'Here is my analysis:\n```json\n{"home_win": 0.6, "draw": 0.25, "away_win": 0.15}\n```\nHope this helps!'
    parsed, warnings = parse_ai_response(raw)

    assert parsed is not None
    assert abs(parsed.home_win - 0.6) < 1e-6
    assert abs(parsed.draw - 0.25) < 1e-6
    assert abs(parsed.away_win - 0.15) < 1e-6


def test_invalid_json_returns_none():
    """Completely invalid JSON returns (None, warnings)."""
    raw = "This is not JSON at all, just plain text."
    parsed, warnings = parse_ai_response(raw)

    assert parsed is None
    assert any("parse_failed" in w for w in warnings)


def test_probabilities_not_summing_to_one_are_normalized():
    """Probabilities that don't sum to ~1.0 get normalized with a warning."""
    raw = '{"home_win": 0.6, "draw": 0.3, "away_win": 0.3}'
    parsed, warnings = parse_ai_response(raw)

    assert parsed is not None
    # Sum was 1.2, so should be normalized
    assert parsed.was_normalized is True
    assert abs(parsed.home_win - 0.5) < 1e-6
    assert abs(parsed.draw - 0.25) < 1e-6
    assert abs(parsed.away_win - 0.25) < 1e-6
    assert any("normalized" in w for w in warnings)


def test_missing_required_fields_returns_none():
    """Missing home_win/draw/away_win returns (None, warnings)."""
    raw = '{"home_win": 0.5, "draw": 0.5}'
    parsed, warnings = parse_ai_response(raw)

    assert parsed is None
    assert any("missing_fields" in w for w in warnings)


def test_xg_data_in_response():
    """xG fields in the response don't break parsing (they are ignored by parser)."""
    raw = '{"home_win": 0.4, "draw": 0.3, "away_win": 0.3, "home_xg": 1.5, "away_xg": 0.8}'
    parsed, warnings = parse_ai_response(raw)

    assert parsed is not None
    assert abs(parsed.home_win - 0.4) < 1e-6
    assert abs(parsed.draw - 0.3) < 1e-6
    assert abs(parsed.away_win - 0.3) < 1e-6
    # xG fields are not part of AIParsedOutput, so they're simply ignored


def test_reasoning_text_is_captured():
    """The reason field and other text fields are captured in the output."""
    raw = '''{
        "home_win": 0.55,
        "draw": 0.25,
        "away_win": 0.20,
        "confidence": 0.85,
        "reason": "Home team has strong form and home advantage in this fixture.",
        "risk_flags": ["injury_key_player"],
        "key_factors": ["home_advantage", "recent_form"],
        "uncertainties": ["weather_conditions"],
        "disagreement_with_system": "System leans draw but AI favors home",
        "disagreement_with_market": "Market slightly favors away",
        "recommended_label": "home_win"
    }'''
    parsed, warnings = parse_ai_response(raw)

    assert parsed is not None
    assert parsed.reason == "Home team has strong form and home advantage in this fixture."
    assert parsed.confidence == 0.85
    assert parsed.risk_flags == ["injury_key_player"]
    assert parsed.key_factors == ["home_advantage", "recent_form"]
    assert parsed.uncertainties == ["weather_conditions"]
    assert parsed.recommended_label == "home_win"
    assert parsed.disagreement_with_system == "System leans draw but AI favors home"
    assert parsed.disagreement_with_market == "Market slightly favors away"
    assert "low_explainability" not in warnings
