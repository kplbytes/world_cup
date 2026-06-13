"""Market data service: divergence computation between model and market."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.models import MarketSnapshot, Match, Team
from app.providers.sporttery import (
    MarketProbability,
    SportteryUnavailable,
    normalize_had_prices,
    parse_response,
)

_SPORTTERY_URL = "https://webapi.sporttery.cn/gateway/uniform/football/getMatchCalculatorV1.qry"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
_TIMEOUT = 15.0


@dataclass(frozen=True)
class DivergenceResult:
    home_diff: float
    draw_diff: float
    away_diff: float
    max_divergence: float
    level: str  # 低 / 中 / 高


def compute_divergence(
    model_pred: dict[str, float],
    market_snap: MarketSnapshot,
) -> DivergenceResult:
    """Compute divergence between model probabilities and market implied probabilities."""
    home_diff = model_pred.get("home_win", 0.0) - market_snap.home_probability
    draw_diff = model_pred.get("draw", 0.0) - market_snap.draw_probability
    away_diff = model_pred.get("away_win", 0.0) - market_snap.away_probability

    max_div = max(abs(home_diff), abs(draw_diff), abs(away_diff))
    if max_div < 0.08:
        level = "低"
    elif max_div < 0.18:
        level = "中"
    else:
        level = "高"

    return DivergenceResult(
        home_diff=home_diff,
        draw_diff=draw_diff,
        away_diff=away_diff,
        max_divergence=max_div,
        level=level,
    )


def divergence_level(max_divergence: float) -> str:
    """Standalone helper to classify divergence level from a max divergence value."""
    if max_divergence < 0.08:
        return "低"
    elif max_divergence < 0.18:
        return "中"
    return "高"


class SportteryRemoteProvider:
    """Fetches HAD odds from Sporttery's public web API."""

    def __init__(self, timeout: float = _TIMEOUT):
        self._timeout = timeout

    def fetch_odds(self) -> list[dict[str, Any]]:
        """Fetch raw match data with HAD odds from Sporttery.

        Returns a list of dicts with keys: match_num, home_team, away_team,
        match_date, had_home, had_draw, had_away.
        Raises SportteryUnavailable on any failure.
        """
        try:
            resp = httpx.get(
                _SPORTTERY_URL,
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/json"},
                timeout=self._timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise SportteryUnavailable(f"Sporttery HTTP error: {exc}") from exc

        payload = parse_response(resp.text)
        return self._extract_matches(payload)

    def _extract_matches(self, payload: dict) -> list[dict[str, Any]]:
        """Extract match data from the parsed Sporttery response."""
        value = payload.get("value", {})
        match_list = value.get("matchInfoList") or value.get("matchCalcList") or []
        results = []
        for item in match_list:
            match_info = item.get("matchInfo", item)
            try:
                home = match_info.get("homeTeamName", "")
                away = match_info.get("awayTeamName", "")
                match_date = match_info.get("matchDate", "")
                match_num = str(match_info.get("matchNum", item.get("matchNumStr", "")))
                had = match_info.get("had", {})
                if not had:
                    # Try nested structure
                    odds_info = item.get("matchOddsInfo", {})
                    had = odds_info.get("had", {})
                had_home = float(had.get("h", 0))
                had_draw = float(had.get("d", 0))
                had_away = float(had.get("a", 0))
                if had_home > 1.0 and had_draw > 1.0 and had_away > 1.0:
                    results.append({
                        "match_num": match_num,
                        "home_team": home,
                        "away_team": away,
                        "match_date": match_date,
                        "had_home": had_home,
                        "had_draw": had_draw,
                        "had_away": had_away,
                    })
            except (ValueError, TypeError, KeyError):
                continue
        return results


def fetch_and_store_market_data(
    session,
    team_aliases: dict[str, str] | None = None,
) -> int:
    """Fetch Sporttery odds and store as MarketSnapshot rows.

    Returns the number of matches successfully matched and stored.
    Raises SportteryUnavailable if the API is unreachable.
    """
    from sqlalchemy import select as sa_select

    provider = SportteryRemoteProvider()
    raw_matches = provider.fetch_odds()

    # Build canonical match lookup: (date_prefix, home_lower, away_lower) → match_id
    matches = list(session.scalars(sa_select(Match).where(Match.status != "final")))
    teams = {t.id: t for t in session.scalars(sa_select(Team))}

    match_lookup: dict[tuple[str, str, str], Match] = {}
    for m in matches:
        home = teams.get(m.home_team_id)
        away = teams.get(m.away_team_id)
        if home and away:
            date_prefix = m.kickoff.strftime("%Y-%m-%d") if m.kickoff else ""
            key = (date_prefix, home.name.lower(), away.name.lower())
            match_lookup[key] = m

    # Also build alias lookup
    aliases = team_aliases or {}

    stored = 0
    for raw in raw_matches:
        market = normalize_had_prices(raw["had_home"], raw["had_draw"], raw["had_away"])

        # Try to match by date + team names
        date_str = raw["match_date"][:10] if raw["match_date"] else ""
        home_name = raw["home_team"].lower()
        away_name = raw["away_team"].lower()

        matched = None
        for (d, h, a), m in match_lookup.items():
            if d != date_str:
                continue
            # Fuzzy match: check if either canonical name contains the sporttery name or vice versa
            if _fuzzy_match(h, home_name) and _fuzzy_match(a, away_name):
                matched = m
                break

        if matched is None:
            continue

        # Upsert: replace existing snapshots for this match from sporttery
        existing = session.scalar(
            sa_select(MarketSnapshot).where(
                MarketSnapshot.match_id == matched.id,
                MarketSnapshot.provider == "sporttery",
            )
        )
        if existing:
            existing.home_probability = market.home
            existing.draw_probability = market.draw
            existing.away_probability = market.away
            existing.raw_overround = market.raw_overround
        else:
            session.add(MarketSnapshot(
                match_id=matched.id,
                provider="sporttery",
                home_probability=market.home,
                draw_probability=market.draw,
                away_probability=market.away,
                raw_overround=market.raw_overround,
                source_match_id=raw["match_num"],
            ))
        stored += 1

    session.flush()
    return stored


def _fuzzy_match(canonical: str, sporttery: str) -> bool:
    """Fuzzy match between canonical team name and Sporttery team name."""
    if canonical == sporttery:
        return True
    # Check containment in either direction
    if canonical in sporttery or sporttery in canonical:
        return True
    # Check common abbreviations / aliases
    return False
