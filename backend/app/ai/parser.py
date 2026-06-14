"""Parse and validate AI model outputs."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.ai.schemas import AIParsedOutput

logger = logging.getLogger(__name__)


def parse_ai_response(raw_text: str | None) -> tuple[AIParsedOutput | None, list[str]]:
    """Parse raw AI response text into a structured output.

    Returns:
        Tuple of (parsed_output, warnings).
        If parsing fails, returns (None, warnings).
    """
    warnings: list[str] = []

    if not raw_text or not raw_text.strip():
        warnings.append("empty_response")
        return None, warnings

    # Try to extract JSON from the response
    text = raw_text.strip()

    # Handle markdown code blocks
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()
    elif "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            text = text[start:end].strip()

    # Try to parse JSON
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to find JSON object in the text
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                data = json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                warnings.append(f"parse_failed: {e}")
                return None, warnings
        else:
            warnings.append(f"parse_failed: {e}")
            return None, warnings

    if not isinstance(data, dict):
        warnings.append("invalid_format: expected JSON object")
        return None, warnings

    # Validate required fields
    missing_fields = []
    for field in ["home_win", "draw", "away_win"]:
        if field not in data:
            missing_fields.append(field)
    if missing_fields:
        warnings.append(f"missing_fields: {missing_fields}")
        return None, warnings

    # Extract and validate probabilities
    try:
        home_win = float(data["home_win"])
        draw = float(data["draw"])
        away_win = float(data["away_win"])
    except (TypeError, ValueError) as e:
        warnings.append(f"invalid_probabilities: {e}")
        return None, warnings

    # Check for negative or > 1 probabilities
    if any(p < 0 or p > 1.5 for p in [home_win, draw, away_win]):
        warnings.append(f"probabilities_out_of_range: hw={home_win}, d={draw}, aw={away_win}")
        return None, warnings

    # Normalize probabilities
    total = home_win + draw + away_win
    was_normalized = False
    if total <= 0:
        warnings.append("probabilities_sum_to_zero")
        return None, warnings
    if abs(total - 1.0) > 0.05:
        home_win /= total
        draw /= total
        away_win /= total
        was_normalized = True
        warnings.append(f"normalized: original_sum={total:.4f}")

    # Confidence
    confidence = 0.5
    confidence_was_default = False
    if "confidence" in data:
        try:
            confidence = float(data["confidence"])
            if confidence < 0 or confidence > 1:
                confidence = 0.5
                confidence_was_default = True
                warnings.append("confidence_out_of_range")
        except (TypeError, ValueError):
            confidence_was_default = True
            warnings.append("confidence_missing")
    else:
        confidence_was_default = True
        warnings.append("confidence_missing")

    # Optional string fields
    reason = str(data.get("reason", ""))
    if not reason.strip():
        warnings.append("low_explainability")

    disagreement_system = str(data.get("disagreement_with_system", ""))
    disagreement_market = str(data.get("disagreement_with_market", ""))
    recommended_label = str(data.get("recommended_label", "uncertain"))

    # Validate recommended_label
    if recommended_label not in ("home_win", "draw", "away_win", "uncertain"):
        recommended_label = "uncertain"

    # List fields
    risk_flags = _parse_string_list(data.get("risk_flags", []))
    key_factors = _parse_string_list(data.get("key_factors", []))
    uncertainties = _parse_string_list(data.get("uncertainties", []))
    profile_factors = _parse_string_list(data.get("profile_factors", []))
    profile_risk_flags = _parse_string_list(data.get("profile_risk_flags", []))

    parsed = AIParsedOutput(
        home_win=home_win,
        draw=draw,
        away_win=away_win,
        confidence=confidence,
        risk_flags=risk_flags,
        key_factors=key_factors,
        reason=reason,
        uncertainties=uncertainties,
        disagreement_with_system=disagreement_system,
        disagreement_with_market=disagreement_market,
        recommended_label=recommended_label,
        profile_factors=profile_factors,
        profile_risk_flags=profile_risk_flags,
        was_normalized=was_normalized,
        confidence_was_default=confidence_was_default,
        parse_warnings=warnings,
    )

    return parsed, warnings


def _parse_string_list(value: Any) -> list[str]:
    """Parse a value into a list of strings."""
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [value]
    return []
